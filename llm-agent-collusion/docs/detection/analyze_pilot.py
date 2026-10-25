"""Read-only, label-free analysis of an exploratory pilot; never invokes inference.

Run from llm-agent-collusion: python docs/detection/analyze_pilot.py PATH_TO_PILOT
The callable analyze(Path(...)) writes only PATH_TO_PILOT/analysis/ artifacts.
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.collusion_detection.config import StudyConfig
from src.collusion_detection.evaluation import _episodes, evaluate
from src.collusion_detection.manifests import verify_predictions
from src.collusion_detection.schemas import Episode
from src.collusion_detection.storage import digest, read_jsonl, write_json


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def summarize_ledger(attempts):
    """Sum actual persisted attempt rows once, independently of cached attribution.

    A repeated cache key can represent another paid attempt after a failed run;
    therefore it must not be deduplicated. Cache-hit calls add no attempt rows.
    """
    result = {"attempts": len(attempts), "source": "run/cache/attempts.jsonl, each persisted attempt row once"}
    for name, field in (
        ("billed_cost_usd", "cost_usd"),
        ("latency_seconds", "latency_seconds"),
        ("input_tokens", "input_tokens"),
        ("output_tokens", "output_tokens"),
    ):
        known = [row[field] for row in attempts if _number(row.get(field))]
        result[name] = {
            "total": sum(known) if attempts and len(known) == len(attempts) else None,
            "known_subtotal": sum(known) if known else None,
            "known_attempts": len(known),
            "unknown_attempts": len(attempts) - len(known),
        }
    token_rows = [
        dict(row, tokens=row["input_tokens"] + row["output_tokens"])
        if _number(row.get("input_tokens")) and _number(row.get("output_tokens"))
        else dict(row, tokens=None)
        for row in attempts
    ]
    known = [row["tokens"] for row in token_rows if row["tokens"] is not None]
    result["tokens"] = {
        "total": sum(known) if attempts and len(known) == len(attempts) else None,
        "known_subtotal": sum(known) if known else None,
        "known_attempts": len(known),
        "unknown_attempts": len(attempts) - len(known),
    }
    return result


def coverage(predictions, episode_ids, conditions, replicates):
    """Report missing, unexpected and duplicate prediction cells explicitly."""
    expected = {
        (eid, condition, replicate)
        for eid in episode_ids
        for condition in conditions
        for replicate in range(replicates)
    }
    observed = Counter((p["episode_id"], p["condition"], p.get("replicate", 0)) for p in predictions)

    def rows(keys):
        return [
            {"episode_id": eid, "condition": condition, "replicate": replicate}
            for eid, condition, replicate in sorted(keys)
        ]

    missing, unexpected = expected - observed.keys(), observed.keys() - expected
    duplicates = [key for key, count in observed.items() if count > 1]
    return {
        "status": "complete" if not missing and not unexpected and not duplicates else "incomplete",
        "expected_predictions": len(expected),
        "observed_predictions": len(predictions),
        "missing_predictions": rows(missing),
        "unexpected_predictions": rows(unexpected),
        "duplicate_prediction_cells": rows(duplicates),
    }


def interruption_provenance(progress, pause, concurrency, ledger):
    """Keep interrupted billing unreconciled and separate active time segments."""
    latest = progress.get("elapsed_seconds")
    initial = (pause.get("progress_at_pause") or {}).get("elapsed_seconds") if pause else None
    active = initial + latest if pause and _number(initial) and _number(latest) else latest if not pause else None
    unrecorded_max = pause.get("possible_unrecorded_inflight_attempts_max", concurrency) if pause else 0
    return {
        "active_orchestration_seconds": active,
        "initial_segment_seconds": initial,
        "latest_segment_seconds": latest,
        "diagnostic_gap_included": False,
        "recorded_known_bill_usd": ledger["billed_cost_usd"]["known_subtotal"],
        "recorded_bill_total_usd": ledger["billed_cost_usd"]["total"],
        "overall_bill_usd": None if pause else ledger["billed_cost_usd"]["total"],
        "billing_status": "unreconciled_after_interruption" if pause else "recorded_attempt_costs",
        "billing_reconciliation_required": bool(pause) or ledger["billed_cost_usd"]["unknown_attempts"] > 0,
        "possible_unrecorded_attempts_max": unrecorded_max,
        "billing_caveat": (
            "Interruption may leave started provider requests billed without attempt-ledger entries. "
            "The recorded known bill is not a reconciled overall provider bill."
            if pause
            else "Recorded provider-reported attempt costs; no provider invoice reconciliation performed."
        ),
    }


def _cutoff(thresholds, condition):
    if isinstance(thresholds, dict):
        thresholds = thresholds.get("thresholds", thresholds)
    return thresholds[condition] if isinstance(thresholds, dict) else thresholds


def _valid(record):
    assessment = record.get("assessment") or {}
    return (
        record.get("status") == "ok"
        and _number(assessment.get("implemented_collusion_probability"))
        and 0 <= assessment["implemented_collusion_probability"] <= 1
        and assessment.get("implementation") in ("supported", "not_supported", "insufficient_evidence")
    )


def _pair_descriptives(aggregated, thresholds):
    pairs = [
        ("MAO_RAW", "M_RAW"),
        ("MAO_RAW", "A_RAW"),
        ("MAO_EAE", "MAO_RAW"),
        ("MAO_LINKED", "MAO_EAE"),
        ("MAO_INTERACTIVE", "MAO_LINKED"),
        ("MAO_INTERACTIVE", "MAO_EAE"),
        ("MAO_INTERACTIVE", "MAO_RAW"),
    ]
    result = {}
    for left, right in pairs:
        if left not in aggregated or right not in aggregated:
            continue
        ids = sorted(
            eid
            for eid in aggregated[left].keys() & aggregated[right].keys()
            if aggregated[left][eid]["score"] is not None and aggregated[right][eid]["score"] is not None
        )
        differences = [aggregated[left][eid]["score"] - aggregated[right][eid]["score"] for eid in ids]
        disagreement = sum(
            (aggregated[left][eid]["score"] >= _cutoff(thresholds, left))
            != (aggregated[right][eid]["score"] >= _cutoff(thresholds, right))
            for eid in ids
        )
        result[f"{left}__vs__{right}"] = {
            "left": left,
            "right": right,
            "direction": "left score minus right score",
            "paired_valid_episodes": len(ids),
            "mean_score_difference": mean(differences) if differences else None,
            "median_score_difference": median(differences) if differences else None,
            "binary_disagreements": disagreement,
            "binary_disagreement_denominator": len(ids),
            "interpretation": "Descriptive score/disposition differences; no evidence of improved detection.",
            "episode_differences": dict(zip(ids, differences)),
        }
    return result


def analyze(out: Path):
    """Verify frozen pilot artifacts, then write unlabeled development descriptives."""
    out = Path(out)
    episodes = [Episode.model_validate(row) for row in read_jsonl(out / "episodes.jsonl")]
    config = StudyConfig.model_validate(json.loads((out / "config.json").read_text())).check()
    sampling = json.loads((out / "sampling.json").read_text())
    splits = json.loads((out / "splits.json").read_text())
    source_manifest = json.loads((out / "run/manifest.json").read_text())
    thresholds = source_manifest["thresholds"]
    predictions, manifest = verify_predictions(out / "run/predictions.jsonl", splits, thresholds)
    if manifest["config"] != config.model_dump():
        raise ValueError("Pilot config differs from frozen run")
    if manifest["episode_hash"] != digest([ep.model_dump() for ep in episodes]):
        raise ValueError("Pilot episodes differ from frozen run")
    if set(sampling.get("selected_episode_ids", [ep.episode_id for ep in episodes])) != {
        ep.episode_id for ep in episodes
    }:
        raise ValueError("Pilot episodes differ from prespecified sample")
    if sampling.get("dataset_hash") and sampling["dataset_hash"] != digest([ep.model_dump() for ep in episodes]):
        raise ValueError("Sampling dataset hash mismatch")
    if any(splits.get(ep.episode_id) != "development" for ep in episodes):
        raise ValueError("This pilot analysis requires entirely exploratory/development episodes")
    completeness = coverage(predictions, manifest["episode_ids"], config.conditions, config.replicates)
    metrics = evaluate(predictions, [], splits, thresholds, bootstrap_samples=0, split="development")
    aggregated = _episodes(predictions)
    descriptive = {}
    for condition in config.conditions:
        records = [p for p in predictions if p["condition"] == condition]
        valid = [p for p in records if _valid(p)]
        scores = [row["score"] for row in aggregated.get(condition, {}).values() if row["score"] is not None]
        descriptive[condition] = {
            "completed_predictions": len(records),
            "expected_predictions": len(episodes) * config.replicates,
            "valid_predictions": len(valid),
            "error_predictions": len(records) - len(valid),
            "valid_episodes": len(scores),
            "mean_episode_score": mean(scores) if scores else None,
            "median_episode_score": median(scores) if scores else None,
            "frozen_threshold": _cutoff(thresholds, condition),
            "threshold_positive_episodes": sum(score >= _cutoff(thresholds, condition) for score in scores),
            "threshold_positive_denominator": len(scores),
            "agreement_categories": dict(Counter(p["assessment"].get("agreement", "missing") for p in valid)),
            "implementation_categories": dict(Counter(p["assessment"]["implementation"] for p in valid)),
            "implementation_insufficient_frequency": (
                sum(p["assessment"]["implementation"] == "insufficient_evidence" for p in valid) / len(valid)
                if valid
                else None
            ),
            "agreement_insufficient_frequency": (
                sum(p["assessment"].get("agreement") == "insufficient_evidence" for p in valid) / len(valid)
                if valid
                else None
            ),
            "question_counts": [
                {
                    "episode_id": p["episode_id"],
                    "replicate": p.get("replicate", 0),
                    "questions": sum(
                        turn.get("speaker") == "judge" and turn.get("action") == "ASK" for turn in p.get("exchange", [])
                    ),
                    "status": p["status"],
                }
                for p in records
            ],
            "deployment_usage": metrics["by_condition"].get(condition, {}).get("usage"),
        }
    reuse = []
    keyed = {(p["episode_id"], p["condition"], p.get("replicate", 0)): p for p in predictions}
    for ep in episodes:
        for view in ("M", "MAO"):
            for replicate in range(config.replicates):
                linked = keyed.get((ep.episode_id, view + "_LINKED", replicate))
                interactive = keyed.get((ep.episode_id, view + "_INTERACTIVE", replicate))
                if linked is None or interactive is None:
                    continue
                left, right = linked.get("initial_report_hash"), interactive.get("initial_report_hash")
                reuse.append(
                    {
                        "episode_id": ep.episode_id,
                        "view": view,
                        "replicate": replicate,
                        "linked_hash": left,
                        "interactive_hash": right,
                        "reused": left is not None and left == right,
                    }
                )
    ledger_path = out / "run/cache/attempts.jsonl"
    ledger = summarize_ledger(read_jsonl(ledger_path) if ledger_path.exists() else [])
    progress_path = out / "run/progress.json"
    progress = json.loads(progress_path.read_text()) if progress_path.exists() else {}
    pause_path = out / "diagnostic-pause.json"
    pause = json.loads(pause_path.read_text()) if pause_path.exists() else None
    interruption = interruption_provenance(progress, pause, config.concurrency, ledger)
    errors = Counter((p.get("error") or {}).get("type", "unknown") for p in predictions if not _valid(p))
    summary = {
        "analysis_kind": "exploratory_unlabeled_development_descriptives",
        "coverage": completeness,
        "manifest_hash": manifest["manifest_hash"],
        "study_hash": manifest.get("study_hash"),
        "role_models": {
            role: {
                "provider": config.roles[role].provider,
                "model": config.roles[role].model,
                "context_limit": config.roles[role].context_limit,
                "max_output_tokens": config.roles[role].max_output_tokens,
            }
            for role in ("assistant", "judge")
        },
        "inference_mode": "mocked"
        if predictions and all(p.get("mocked") is True for p in predictions)
        else "hosted"
        if predictions and all(p.get("mocked") is False for p in predictions)
        else "mixed_or_unknown",
        "sampling": sampling,
        "episodes": len(episodes),
        "source_groups": len({e.group_id for e in episodes}),
        "labels": "absent; two independent reviewers and adjudication have not been completed",
        "annotation_links": {
            str(path.relative_to(out)): "../" + str(path.relative_to(out))
            for path in [
                out / "annotations/README.md",
                out / "annotations/packet.md",
                out / "annotations/labels.template.jsonl",
                out / "annotations/reviewer-A.template.jsonl",
                out / "annotations/reviewer-B.template.jsonl",
                out / "annotations/rubric.md",
            ]
            if path.exists()
        },
        "wallclock_elapsed_seconds": interruption["active_orchestration_seconds"],
        "wallclock_basis": "Active orchestration segments summed across the recorded pause/resume; diagnosis gap excluded.",
        "interruption_provenance": interruption,
        "diagnostic_pause": pause,
        "run_progress": progress,
        "by_condition": descriptive,
        "paired_descriptives": _pair_descriptives(aggregated, thresholds),
        "initial_report_reuse": {
            "checked_pairs": len(reuse),
            "matching_pairs": sum(r["reused"] for r in reuse),
            "pairs": reuse,
        },
        "actual_attempt_ledger": ledger,
        "error_types": dict(errors),
        "error_messages": dict(
            Counter((p.get("error") or {}).get("message", "unknown") for p in predictions if not _valid(p))
        ),
        "interpretation_limits": [
            "No human gold labels: no accuracy, confusion matrix, recall, FPR or detection-improvement claim.",
            "Threshold positives are model dispositions, not established implemented collusion.",
            "Scores are ranking scores, not established calibrated probabilities.",
            "All episodes are development/exploratory; the provisional common source group prevents independent held-out claims.",
            "Aggregate call latency sums parallel work and is different from elapsed wallclock time.",
            "Deployment usage attributes initial reports to both LINKED and INTERACTIVE; actual ledger counts each paid attempt once.",
        ],
    }
    destination = out / "analysis"
    destination.mkdir(parents=True, exist_ok=True)
    write_json(destination / "summary.json", summary)
    # The generic evaluator supplied error/usage accounting; its empty binary
    # metric placeholders are deliberately omitted from this unlabeled artifact.
    descriptive_metrics = {
        "split": "development",
        "binary_metrics_reported": False,
        "labels": "absent",
        "replicate_aggregation": metrics["replicate_aggregation"],
        "by_condition": {
            condition: {
                key: row[key]
                for key in (
                    "total_episodes",
                    "total_prediction_calls",
                    "valid_prediction_calls",
                    "error_prediction_calls",
                    "error_only_episodes",
                    "exclusions",
                    "insufficient_evidence",
                    "usage",
                    "data_kinds",
                )
            }
            for condition, row in metrics["by_condition"].items()
        },
    }
    write_json(destination / "metrics.json", descriptive_metrics)
    _write_report(summary, destination)
    _histogram(aggregated, config.conditions, destination)
    return summary


def _format(value):
    return "unknown" if value is None else f"{value:.4f}" if isinstance(value, float) else str(value)


def _write_report(summary, destination):
    coverage_row = summary["coverage"]
    ledger = summary["actual_attempt_ledger"]
    interruption = summary["interruption_provenance"]
    lines = [
        "# Exploratory unlabeled development pilot",
        "",
        "**No human labels or held-out benchmark results. Model dispositions do not establish collusion.**",
        "",
        (
            f"Run coverage: **{coverage_row['status']}**; {coverage_row['observed_predictions']} / "
            f"{coverage_row['expected_predictions']} expected prediction cells. "
            f"Episodes: {summary['episodes']}; provisional source groups: {summary['source_groups']}."
        ),
        "",
        (
            f"Active orchestration time: {_format(summary['wallclock_elapsed_seconds'])} seconds "
            "(recorded running segments; diagnostic gap excluded). "
            f"Actual ledger attempts: {ledger['attempts']}; recorded known bill USD: "
            f"{_format(interruption['recorded_known_bill_usd'])}; overall bill USD: "
            f"{_format(interruption['overall_bill_usd'])}; "
            f"unknown-billing attempts: {ledger['billed_cost_usd']['unknown_attempts']}."
        ),
        "",
        (
            "Summed call latency can exceed wallclock time because calls run in parallel. Cached report costs "
            "are attributed to each deployed protocol; the actual attempt ledger is counted once."
        ),
        (
            f"Sum of actual request latency: {_format(ledger['latency_seconds']['total'])} seconds; "
            f"actual request tokens: {_format(ledger['tokens']['total'])} "
            f"(known subtotal {_format(ledger['tokens']['known_subtotal'])}). "
            f"Inference mode: {summary['inference_mode']}."
        ),
        "",
        "| Condition | Valid / completed | Errors | Valid episodes | Mean score | Median score | Threshold positives / valid episodes |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for condition, row in summary["by_condition"].items():
        lines.append(
            f"| {condition} | {row['valid_predictions']} / {row['completed_predictions']} | "
            f"{row['error_predictions']} | {row['valid_episodes']} | {_format(row['mean_episode_score'])} | "
            f"{_format(row['median_episode_score'])} | {row['threshold_positive_episodes']} / "
            f"{row['threshold_positive_denominator']} |"
        )
    if summary["diagnostic_pause"]:
        lines += [
            "",
            "## Diagnostic interruption and unreconciled billing",
            "",
            (
                f"**The overall provider bill is unknown/unreconciled.** Up to "
                f"{interruption['possible_unrecorded_attempts_max']} in-flight requests may have been billed without "
                "attempt-ledger entries at interruption. Recorded known billing above is a subtotal of recorded "
                "actual attempts, not the reconciled overall bill."
            ),
            "",
            (
                "The pilot was paused for development diagnosis and resumed with the same frozen protocol and "
                "cached outputs. Errors and original scores remain unchanged. Exact-span failures include invented "
                "offsets and altered quotations. A distinct citation-eligibility mismatch can also occur: relationship "
                "event IDs are visible in the report but the forwarding whitelist includes only quoted excerpts. "
                "Such failures must not all be attributed to model hallucination. A future version can include validated "
                "relationship IDs in the eligibility set or explicitly require quote-only citations; that needs a separate "
                "version and re-run, rather than changing this pilot's outcomes."
            ),
            "",
            "Initial segment seconds: "
            + _format(interruption["initial_segment_seconds"])
            + "; resumed segment seconds: "
            + _format(interruption["latest_segment_seconds"])
            + ". The diagnosis gap is excluded from active time.",
            "",
            "Recorded diagnostic provenance: [diagnostic-pause.json](../diagnostic-pause.json).",
            "",
            "<details><summary>Diagnostic pause record</summary>",
            "",
            "```json",
            json.dumps(summary["diagnostic_pause"], indent=2, ensure_ascii=False),
            "```",
            "",
            "</details>",
            "",
        ]
    lines += [
        "",
        (
            "Agreement and implementation category counts, insufficient-evidence responses, per-episode question "
            "counts and deployment usage are in [summary.json](summary.json). Positives use the frozen threshold; "
            "these counts are not accuracy estimates."
        ),
        "",
    ]
    lines += [
        "## Assessment categories and attributed deployment usage",
        "",
        (
            "Category counts are per valid prediction call; question means include all persisted predictions. "
            "Scores above average valid replicates within each episode."
        ),
        "",
        "| Condition | Agreement supported / not / insufficient | Implementation supported / not / insufficient | Mean questions | Tokens | Deployment USD |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for condition, row in summary["by_condition"].items():
        agreement = " / ".join(
            str(row["agreement_categories"].get(key, 0))
            for key in ("supported", "not_supported", "insufficient_evidence")
        )
        implementation = " / ".join(
            str(row["implementation_categories"].get(key, 0))
            for key in ("supported", "not_supported", "insufficient_evidence")
        )
        question_values = [question["questions"] for question in row["question_counts"]]
        usage = row["deployment_usage"] or {}
        lines.append(
            f"| {condition} | {agreement} | {implementation} | "
            f"{_format(mean(question_values) if question_values else None)} | "
            f"{_format(usage.get('tokens', {}).get('total'))} | "
            f"{_format(usage.get('cost', {}).get('total'))} |"
        )
    lines += ["", "## Validation and API failures", "", "| Failure | Count |", "|---|---:|"]
    for message, count in sorted(summary["error_messages"].items(), key=lambda row: -row[1]):
        lines.append(f"| {message.replace('|', '/')} | {count} |")
    lines += ["", "Errors retain no assessment and are never counted as negative predictions.", ""]
    lines += ["", "## Paired descriptive differences", ""]
    for name, row in summary["paired_descriptives"].items():
        lines.append(
            f"- {name}: {row['paired_valid_episodes']} paired episodes; mean left-minus-right score "
            f"{_format(row['mean_score_difference'])}; binary disagreements "
            f"{row['binary_disagreements']} / {row['binary_disagreement_denominator']}. "
            "Direction and disagreement do not show improved detection."
        )
    reuse = summary["initial_report_reuse"]
    lines += [
        "",
        (
            f"LINKED/INTERACTIVE identical initial reports: {reuse['matching_pairs']} / {reuse['checked_pairs']} "
            "completed pairs. Missing hashes count as unmatched."
        ),
        "",
        "## Independent annotation remains required",
        "",
        (
            "Two independent reviewers and adjudication are absent. Review the full observable episodes without "
            "consulting these model outputs or private scenario assignments."
        ),
        "",
        "## Interpretation limits",
        "",
    ]
    links = summary["annotation_links"]
    if links:
        lines += [
            "",
            "Independent annotation material: "
            + ", ".join(f"[{path.split('/')[-1]}]({target})" for path, target in links.items()),
            "",
        ]
    lines += [f"- {line}" for line in summary["interpretation_limits"]]
    if coverage_row["status"] != "complete":
        lines += [
            "",
            (
                "**INCOMPLETE RUN:** missing/unexpected cells are recorded explicitly in summary.json. "
                "This report covers only the persisted predictions available at analysis time."
            ),
        ]
    lines += ["", "![Unlabeled exploratory score distributions](score_histogram.png)", ""]
    (destination / "report.md").write_text("\n".join(lines))


def _histogram(aggregated, conditions, destination):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    columns = 3
    rows = (len(conditions) + columns - 1) // columns
    figure, axes = plt.subplots(rows, columns, figsize=(12, max(3, rows * 2.8)), squeeze=False)
    for ax, condition in zip(axes.flat, conditions):
        scores = [row["score"] for row in aggregated.get(condition, {}).values() if row["score"] is not None]
        ax.hist(scores, bins=[i / 10 for i in range(11)], edgecolor="white")
        ax.set_title(f"{condition} (valid N={len(scores)})")
        ax.set_xlim(0, 1)
        ax.set_xlabel("Uncalibrated model ranking score")
        ax.set_ylabel("Episodes")
    for ax in list(axes.flat)[len(conditions) :]:
        ax.set_visible(False)
    figure.suptitle("Exploratory unlabeled development pilot — no detection-performance claim")
    figure.tight_layout()
    figure.savefig(destination / "score_histogram.png", dpi=150)
    plt.close(figure)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python docs/detection/analyze_pilot.py PATH_TO_PILOT")
    result = analyze(Path(sys.argv[1]))
    print(
        json.dumps(
            {"coverage": result["coverage"]["status"], "actual_attempt_ledger": result["actual_attempt_ledger"]},
            indent=2,
        )
    )
