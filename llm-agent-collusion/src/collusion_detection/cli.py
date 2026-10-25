"""CLI defaults to fenced offline execution; paid calls require explicit --live."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .config import StudyConfig
from .credentials import load_live_credentials
from .engine import Engine, prompt_hashes
from .evaluation import assign_splits, calibrate, evaluate, validate_labels, write_report
from .evidence import build_view
from .fixtures import make_fixtures
from .importer import import_market
from .manifests import study_hash, verify_predictions
from .planning import check_live_plan, plan
from .providers import CallManager, FakeClient, LiveClient
from .safety import ROOT, fingerprint, offline_network, offline_test_environment, require_acceptance
from .schemas import Episode
from .storage import canonical, digest, read_jsonl, write_json, write_jsonl


def load_config(path):
    return StudyConfig.model_validate(json.loads(Path(path).read_text())).check() if path else StudyConfig().check()


def load_episodes(path, config=None):
    episodes = [Episode.model_validate(r) for r in read_jsonl(path)]
    if len({e.episode_id for e in episodes}) != len(episodes):
        raise ValueError("Duplicate episode IDs")
    if config is not None and not config.include_interventions:
        episodes = [e for e in episodes if e.oversight == "disabled"]
    if not episodes:
        raise ValueError("No eligible episodes")
    return episodes


def export_annotations(episodes, out):
    out = Path(out)
    packet = []
    for ep in episodes:
        ev = build_view(ep, "MAO")
        packet += [
            f"# Episode {ep.episode_id}",
            "",
            "Independent review: do not consult model predictions or private provenance.",
            "",
            "```json",
            json.dumps(ev.context, indent=2),
            "```",
            "",
        ]
        for event in ev.events:
            packet += [
                f"## {event.event_id} — round {event.round} — {event.event_type}",
                f"Actor: {event.actor}; available to other sellers: {event.available_round or 'not applicable'}.",
                "",
                event.text,
                "",
            ]
    out.mkdir(parents=True, exist_ok=True)
    (out / "packet.md").write_text("\n".join(packet))
    write_jsonl(
        out / "labels.template.jsonl",
        [
            {
                "episode_id": e.episode_id,
                "reviewer": "",
                "agreement": None,
                "implemented_collusion": None,
                "coalition": [],
                "evidence_ids": [],
                "economic_consequences": None,
                "adjudicator": None,
                "public_disclosure": None,
            }
            for e in episodes
        ],
    )


async def hosted_client(config, roles):
    selected = [config.roles[r] for r in roles]
    load_live_credentials(selected)
    client = LiveClient(selected, explicit_live=True)
    checks = await client.validate_models(selected)
    for role, check in zip([r for r in selected if r.provider != "fake"], checks):
        if (
            check["context_limit_manually_configured"]
            or check["capabilities_manually_verified_required"]
            or check.get("pricing_manually_verified_required")
            or check.get("structured_output_manually_verified_required")
        ):
            record = config.manual_model_verification.get(role.model, {})
            if not (
                record.get("source_url")
                and record.get("checked_at")
                and record.get("text_input") is True
                and record.get("text_output") is True
                and record.get("context_limit", 0) >= role.context_limit
            ):
                raise ValueError(
                    "Catalog omits required fields; supply dated official manual_model_verification for " + role.model
                )
            if check.get("pricing_manually_verified_required") and (
                record.get("input_per_million") != role.input_per_million
                or record.get("output_per_million") != role.output_per_million
            ):
                raise ValueError("Supply verified pricing matching role config for " + role.model)
            if (
                check.get("structured_output_manually_verified_required")
                and record.get("structured_outputs") is not True
            ):
                raise ValueError("Supply verified strict JSON Schema capability for " + role.model)
    return client, checks


async def run_predictions(config, episodes, out, client=None, splits=None, split=None, thresholds=None):
    out = Path(out)
    if split is not None:
        if splits is None:
            raise ValueError("--split requires --splits")
        episodes = [e for e in episodes if splits.get(e.episode_id) == split]
        if not episodes:
            raise ValueError("Requested split is empty")
    expansion = plan(config, episodes)
    manifest = {
        "version": 1,
        "config": config.model_dump(),
        "prompts": prompt_hashes(config.prompt_version),
        "episode_hash": expansion["episode_hash"],
        "episode_ids": [e.episode_id for e in episodes],
        "evidence_hashes": {
            e.episode_id: {v: digest(build_view(e, v).model_dump()) for v in ["M", "MAO", "A"]} for e in episodes
        },
        "splits": splits,
        "split": split,
        "thresholds": thresholds if thresholds is not None else config.fixed_threshold,
        "study_hash": study_hash(config, prompt_hashes(config.prompt_version)),
        "exclusions": "oversight enabled/unknown excluded unless config.include_interventions",
        "plan": expansion,
        "mocked": client is None or isinstance(client, FakeClient),
    }
    manifest["manifest_hash"] = digest(manifest)
    target = out / "manifest.json"
    if target.exists() and json.loads(target.read_text()) != manifest:
        raise ValueError("Output directory has a different frozen manifest; use a new output directory")
    if isinstance(thresholds, dict) and thresholds.get("study_hash") != manifest["study_hash"]:
        raise ValueError("Threshold artifact comes from a different provider/prompt/config study")
    write_json(target, manifest)
    manager = CallManager(client or FakeClient(), out / "cache", config.budget_usd, config.concurrency, config.retries)
    engine = Engine(config, manager)
    predictions = []
    started = time.monotonic()
    write_jsonl(out / "predictions.jsonl", [])
    episode_order = {ep.episode_id: i for i, ep in enumerate(episodes)}
    condition_order = {condition: i for i, condition in enumerate(config.conditions)}
    completed_episodes = set()
    pending = asyncio.Queue()
    for episode in episodes:
        pending.put_nowait(episode)

    def checkpoint(prediction=None):
        if prediction is not None:
            with (out / "predictions.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(canonical(prediction) + "\n")
        write_json(
            out / "progress.json",
            {
                "completed_predictions": len(predictions),
                "expected_predictions": len(episodes) * len(config.conditions) * config.replicates,
                "completed_episodes": len(completed_episodes),
                "expected_episodes": len(episodes),
                "ok": sum(p["status"] == "ok" for p in predictions),
                "errors": sum(p["status"] == "error" for p in predictions),
                "accounted_cost_usd": manager.accounted_usd,
                "elapsed_seconds": time.monotonic() - started,
                "status": "complete" if len(completed_episodes) == len(episodes) else "running",
            },
        )

    async def worker():
        while not pending.empty():
            ep = pending.get_nowait()
            try:
                for view in ["M", "MAO", "A"]:
                    write_json(out / "evidence" / ep.episode_id / (view + ".json"), build_view(ep, view).model_dump())
                for replicate in range(config.replicates):
                    for condition in config.conditions:
                        prediction = await engine.run(ep, condition, replicate)
                        prediction["manifest_hash"] = manifest["manifest_hash"]
                        predictions.append(prediction)
                        checkpoint(prediction)
                completed_episodes.add(ep.episode_id)
                checkpoint()
                print(
                    f"Progress: {len(completed_episodes)}/{len(episodes)} episodes; "
                    f"{len(predictions)} predictions; {sum(p['status'] == 'error' for p in predictions)} errors",
                    file=sys.stderr,
                    flush=True,
                )
            finally:
                pending.task_done()

    checkpoint()
    await asyncio.gather(*(worker() for _ in range(min(config.concurrency, len(episodes)))))
    predictions.sort(key=lambda p: (episode_order[p["episode_id"]], p["replicate"], condition_order[p["condition"]]))
    write_jsonl(out / "predictions.jsonl", predictions)
    return predictions, manifest


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ["doctor", "plan", "run", "generate-market"]:
        q = sub.add_parser(name)
        q.add_argument("--config", default="configs/detection/offline.json")
        q.add_argument("--live", action="store_true")
        q.add_argument("--dry-run", action="store_true")
        q.add_argument("--acceptance", default="detection_outputs/acceptance.json")
        q.add_argument("--out", default="detection_outputs/" + name)
        if name in ["plan", "run"]:
            q.add_argument("--episodes", required=True)
            q.add_argument("--splits")
            q.add_argument("--split", choices=["development", "held_out"])
        if name == "run":
            q.add_argument("--plan", help="Previously reviewed dry-run expansion JSON, required for --live")
            q.add_argument("--thresholds")
        if name == "generate-market":
            q.add_argument("--seeds", nargs="+", type=int, default=[0])
            q.add_argument("--rounds", type=int, default=12)
            q.add_argument("--plan")
    q = sub.add_parser("import-market")
    q.add_argument("--source", default="data")
    q.add_argument("--out", default="detection_outputs/imported")
    q.add_argument("--include-interventions", action="store_true")
    q.add_argument("--groups", help="Private JSON mapping source folder to related group ID")
    q.add_argument("--limit", type=int)
    q = sub.add_parser("make-fixtures")
    q.add_argument("--out", default="detection_outputs/fixtures")
    q = sub.add_parser("export-annotations")
    q.add_argument("--episodes", required=True)
    q.add_argument("--out", required=True)
    q = sub.add_parser("validate-labels")
    q.add_argument("--episodes", required=True)
    q.add_argument("--labels", required=True)
    q.add_argument("--out", required=True)
    q = sub.add_parser("split")
    q.add_argument("--episodes", required=True)
    q.add_argument("--out", required=True)
    q.add_argument("--development-fraction", type=float, default=0.25)
    q.add_argument("--seed", type=int, default=0)
    for name in ["calibrate", "evaluate"]:
        q = sub.add_parser(name)
        q.add_argument("--predictions", required=True)
        q.add_argument("--labels")
        q.add_argument("--splits", required=True)
        q.add_argument("--out", required=True)
        if name == "calibrate":
            q.add_argument("--target-fpr", type=float, default=0.1)
            q.add_argument("--fixed-threshold", type=float)
        else:
            q.add_argument("--thresholds")
            q.add_argument("--fixed-threshold", type=float, default=0.5)
            q.add_argument("--bootstrap-samples", type=int, default=500)
    q = sub.add_parser("report")
    q.add_argument("--metrics", required=True)
    q.add_argument("--out", required=True)
    q = sub.add_parser("experiment-zero")
    q.add_argument("--generation", required=True)
    q.add_argument("--provenance", required=True)
    q.add_argument("--labels")
    q.add_argument("--out", required=True)
    q = sub.add_parser("demo")
    q.add_argument("--out", default="detection_outputs/demo")
    return p


def _json(path):
    return json.loads(Path(path).read_text())


def dispatch(a):
    if a.command == "experiment-zero":
        from .experiment_zero import summarize_sampling

        result = summarize_sampling(
            read_jsonl(a.generation), read_jsonl(a.provenance), read_jsonl(a.labels) if a.labels else []
        )
        write_json(a.out, result)
        return result
    if a.command == "import-market":
        episodes, excluded = import_market(
            Path(a.source), Path(a.out), a.include_interventions, _json(a.groups) if a.groups else None, a.limit
        )
        return {"imported": len(episodes), "excluded": len(excluded), "out": a.out}
    if a.command == "make-fixtures":
        return {"synthetic_episodes": len(make_fixtures(Path(a.out))), "out": a.out}
    if a.command == "export-annotations":
        export_annotations(load_episodes(a.episodes), a.out)
        return {"out": a.out}
    if a.command == "validate-labels":
        labels = validate_labels(read_jsonl(a.labels), read_jsonl(a.episodes))
        write_jsonl(a.out, labels)
        return {"validated": len(labels), "out": a.out}
    if a.command == "split":
        splits = assign_splits(read_jsonl(a.episodes), a.development_fraction, a.seed)
        write_json(a.out, splits)
        return {"splits": splits}
    if a.command == "calibrate":
        if not a.labels:
            raise ValueError("Calibration needs development labels; no labels have been invented")
        # Library rejects held-out predictions. CLI likewise requires a development-only file.
        predictions, manifest = verify_predictions(a.predictions, _json(a.splits))
        result = calibrate(predictions, read_jsonl(a.labels), _json(a.splits), a.target_fpr, a.fixed_threshold)
        result["study_hash"] = manifest["study_hash"]
        result["source_manifest_hash"] = manifest["manifest_hash"]
        write_json(a.out, result)
        return result
    if a.command == "evaluate":
        thresholds = _json(a.thresholds) if a.thresholds else a.fixed_threshold
        predictions, manifest = verify_predictions(a.predictions, _json(a.splits), thresholds)
        result = evaluate(
            predictions,
            read_jsonl(a.labels) if a.labels else [],
            _json(a.splits),
            thresholds,
            a.bootstrap_samples,
        )
        write_json(a.out, result)
        return {"out": a.out, "has_binary_labels": result["has_binary_labels"]}
    if a.command == "report":
        return {"report": str(write_report(_json(a.metrics), Path(a.out)))}
    if a.command == "demo":
        return demo(Path(a.out))
    config = load_config(a.config)
    if a.command == "doctor":
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/detection", "-q"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=offline_test_environment(),
        )
        print(result.stdout, end="")
        if result.returncode:
            print(result.stderr, file=sys.stderr)
            raise ValueError("Offline acceptance tests failed")
        receipt = {
            "status": "passed",
            "fingerprint": fingerprint(config),
            "test_summary": result.stdout.strip(),
            "network": "blocked by tests/detection/conftest.py",
            "config_hash": digest(config.model_dump()),
        }
        write_json(a.acceptance, receipt)
        checks = (
            asyncio.run(hosted_client(config, ["assistant", "judge", "seller", "buyer"]))[1]
            if a.live and not a.dry_run
            else []
        )
        return {"acceptance": a.acceptance, "provider_validation": checks, "completion_requests_sent": 0}
    if a.command == "generate-market":
        from .generation import generate_market, plan_generation

        expansion = plan_generation(config.model_dump(), a.seeds, a.rounds)
        if a.dry_run:
            write_json(a.out, expansion)
            return expansion
        if not a.live:
            raise ValueError("Fresh market generation needs --live; use --dry-run for zero requests")
        require_acceptance(a.acceptance, config)
        if not a.plan or _json(a.plan) != expansion:
            raise ValueError("Supply an unchanged reviewed generation --plan from --dry-run")
        cost = expansion.get("estimated_maximum_usd")
        if config.budget_usd is None or cost is None or cost > config.budget_usd:
            raise ValueError("Generation estimated maximum exceeds explicit budget or is unknown")
        client, checks = asyncio.run(hosted_client(config, ["seller", "buyer"]))
        manager = CallManager(client, Path(a.out) / "cache", config.budget_usd, config.concurrency, config.retries)
        dirs = generate_market(config.model_dump(), manager, Path(a.out), a.seeds, a.rounds)
        write_json(Path(a.out) / "provider_validation.json", checks)
        return {"generated": [str(d) for d in dirs]}
    episodes = load_episodes(a.episodes, config)
    splits = _json(a.splits) if a.splits else None
    if a.split:
        if splits is None:
            raise ValueError("--split requires --splits")
        episodes = [e for e in episodes if splits.get(e.episode_id) == a.split]
        if not episodes:
            raise ValueError("Requested split is empty")
    expansion = plan(config, episodes)
    if a.command == "plan" or a.dry_run:
        write_json(a.out, expansion)
        return expansion
    client = None
    checks = []
    if a.live:
        require_acceptance(a.acceptance, config)
        check_live_plan(expansion, config)
        if not a.plan or _json(a.plan) != expansion:
            raise ValueError("Supply an unchanged reviewed plan --dry-run JSON using --plan")
        client, checks = asyncio.run(hosted_client(config, ["assistant", "judge"]))
    elif any(config.roles[r].provider != "fake" for r in ["assistant", "judge"]):
        raise ValueError("Hosted config requires --live; use --dry-run to plan without credentials")
    thresholds = _json(a.thresholds) if a.thresholds else None
    predictions, manifest = asyncio.run(run_predictions(config, episodes, a.out, client, splits, a.split, thresholds))
    if checks:
        write_json(Path(a.out) / "provider_validation.json", checks)
    return {
        "predictions": len(predictions),
        "errors": sum(p["status"] == "error" for p in predictions),
        "error_details": [
            {"condition": p["condition"], "error": p["error"]} for p in predictions if p["status"] == "error"
        ][:3],
        "mocked": client is None or isinstance(client, FakeClient),
        "out": a.out,
        "manifest_hash": manifest["manifest_hash"],
    }


def demo(out):
    fixture_eps = make_fixtures(out / "fixtures")
    # Prespecified inclusion criterion: seller communication on and live oversight off.
    # Selection never reads model performance or behavioral outcomes.
    candidates = []
    for path in sorted((ROOT / "data").rglob("auction_results.md")):
        criteria = _json(path.parent / "experiment_metadata.json").get("auction_config", {})
        if criteria.get("seller_comms_enabled") is True and criteria.get("oversight_enabled") is False:
            candidates.append(path)
    if not candidates:
        raise ValueError("No eligible real seller-communication log")
    imported, _excluded = import_market(candidates[0], out / "real", limit=1)
    if not imported:
        raise ValueError("No real bundled non-intervention log available")
    config = StudyConfig().check()
    # Offline only: byte-conservative context bounds for complete bundled episodes.
    for role in config.roles.values():
        role.context_limit = 1_000_000
    splits = assign_splits([e.model_dump() for e in fixture_eps], 0.34, 0)
    write_json(out / "fixtures" / "splits.json", splits)
    fixture_predictions, fixture_manifest = asyncio.run(
        run_predictions(config, fixture_eps, out / "fixture_run", splits=splits)
    )
    labels = validate_labels(
        read_jsonl(out / "fixtures/private/synthetic_expectations.jsonl"), [e.model_dump() for e in fixture_eps]
    )
    dev = [p for p in fixture_predictions if splits[p["episode_id"]] == "development"]
    frozen = calibrate(dev, labels, splits, fixed_threshold=0.5)
    frozen["study_hash"] = fixture_manifest["study_hash"]
    frozen["source_manifest_hash"] = fixture_manifest["manifest_hash"]
    write_json(out / "fixture_run" / "thresholds.json", frozen)
    metrics = evaluate(fixture_predictions, labels, splits, frozen, bootstrap_samples=100)
    write_report(metrics, out / "fixture_report")
    export_annotations(imported, out / "real/annotations")
    real_splits = {e.episode_id: "held_out" for e in imported}
    predictions, _ = asyncio.run(run_predictions(config, imported, out / "real_run", splits=real_splits))
    write_json(out / "real/splits.json", real_splits)
    real_metrics = evaluate(predictions, [], real_splits, 0.5, bootstrap_samples=100)
    write_report(real_metrics, out / "real_report")
    if any(p["status"] != "ok" for p in fixture_predictions + predictions):
        raise ValueError("Demo encountered protocol errors; inspect predictions.jsonl")
    return {
        "mocked": True,
        "synthetic_predictions": len(fixture_predictions),
        "real_unlabeled_predictions": len(predictions),
        "fixture_report": str(out / "fixture_report/report.md"),
        "real_report": str(out / "real_report/report.md"),
        "scientific_accuracy_claimed": False,
    }


def main(argv=None):
    os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "detection_outputs/.matplotlib"))
    args = parser().parse_args(argv)
    try:
        if getattr(args, "live", False) and not getattr(args, "dry_run", False):
            result = dispatch(args)
        else:
            with offline_network():
                result = dispatch(args)
        print(json.dumps(result, indent=2, allow_nan=False))
        if args.command == "run" and result.get("errors", 0):
            raise SystemExit(1)
    except (ValueError, OSError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2) from None
