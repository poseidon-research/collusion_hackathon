"""Private annotation, development calibration and grouped held-out evaluation.

The probability is treated as a ranking score. Repeated predictions are averaged
within an episode; uncertainty resamples related episode groups, never calls.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from .schemas import PrivateLabel


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def assign_splits(episodes: list[dict], development_fraction: float = 0.25, seed: int = 0) -> dict[str, str]:
    """Split whole connected groups (including parent/branch links) before tuning."""
    if not 0 < development_fraction < 1:
        raise ValueError("development_fraction must be strictly between zero and one")
    ids = [str(e["episode_id"]) for e in episodes]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate episode IDs")
    parent = {eid: eid for eid in ids}

    def root(eid: str) -> str:
        while parent[eid] != eid:
            parent[eid] = parent[parent[eid]]
            eid = parent[eid]
        return eid

    def union(a: str, b: str) -> None:
        parent[root(a)] = root(b)

    groups: dict[str, str] = {}
    for episode in episodes:
        eid = str(episode["episode_id"])
        group = str(episode.get("group_id") or eid)
        if group in groups:
            union(eid, groups[group])
        groups[group] = eid
        for field in ("parent_episode_id", "branch_of", "root_episode_id"):
            related = episode.get(field)
            if related is not None and str(related) in parent:
                union(eid, str(related))
    members: dict[str, list[str]] = defaultdict(list)
    for eid in sorted(ids):
        members[root(eid)].append(eid)
    clusters = sorted(members.values())
    random.Random(seed).shuffle(clusters)
    count = round(len(clusters) * development_fraction)
    if len(clusters) > 1:
        count = max(1, min(count, len(clusters) - 1))
    return {eid: "development" if i < count else "held_out" for i, group in enumerate(clusters) for eid in group}


def validate_labels(labels: list[dict], episodes: list[dict]) -> list[dict]:
    """Check private human labels and references; resolve explicit adjudications only.

    Independent reviewers may agree. Disagreements require an explicit adjudicator
    record; duplicate submissions by a reviewer require adjudication as well.
    """
    sources = {str(e["episode_id"]): e for e in episodes}
    if len(sources) != len(episodes):
        raise ValueError("duplicate episode IDs")
    grouped: dict[str, list[dict]] = defaultdict(list)
    for raw in labels:
        PrivateLabel.model_validate(raw)
        label = dict(raw)
        eid = str(label.get("episode_id", ""))
        if eid not in sources:
            raise ValueError(f"unknown episode in label: {eid}")
        if not isinstance(label.get("reviewer"), str) or not label["reviewer"].strip():
            raise ValueError(f"reviewer is required for {eid}; do not invent human annotations")
        if label.get("agreement") not in (None, "supported", "not_supported", "insufficient_evidence"):
            raise ValueError(f"invalid agreement label for {eid}")
        if label.get("implemented_collusion") not in (None, "positive", "negative", "ambiguous"):
            raise ValueError(f"invalid implemented_collusion label for {eid}")
        for field in ("coalition", "evidence_ids"):
            value = label.get(field, [])
            if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
                raise ValueError(f"{field} must be a list of strings for {eid}")
            if len(set(value)) != len(value):
                raise ValueError(f"duplicate {field} for {eid}")
            label[field] = value
        for field in ("economic_consequences", "adjudicator"):
            if label.get(field) is not None and not isinstance(label[field], str):
                raise ValueError(f"{field} must be text or null for {eid}")
        episode = sources[eid]
        event_ids = {str(e.get("event_id", e.get("id", ""))) for e in episode.get("events", [])}
        if set(label["evidence_ids"]) - event_ids:
            raise ValueError(f"unknown evidence IDs in label for {eid}")
        participants = episode.get("participants", [])
        if isinstance(participants, dict):
            aliases = {
                alias
                for alias, participant in participants.items()
                if not isinstance(participant, dict) or participant.get("role", "seller") == "seller"
            }
        else:
            aliases = {
                p if isinstance(p, str) else str(p.get("alias", p.get("participant_id", p.get("id", ""))))
                for p in participants
                if isinstance(p, str) or p.get("role", "seller") == "seller"
            }
        if participants and set(label["coalition"]) - aliases:
            raise ValueError(f"unknown or nonseller coalition participant for {eid}")
        label["episode_id"] = eid
        grouped[eid].append(label)
    resolved = []
    for eid, records in sorted(grouped.items()):
        adjudicated = [r for r in records if r.get("adjudicator", "") and r["adjudicator"].strip()]
        decisions = {
            (r.get("implemented_collusion"), r.get("agreement"), tuple(sorted(r["coalition"]))) for r in records
        }
        duplicate_reviewers = len({r["reviewer"] for r in records}) != len(records)
        if len(adjudicated) > 1:
            raise ValueError(f"multiple adjudication records for {eid}")
        if (len(decisions) > 1 or duplicate_reviewers) and not adjudicated:
            raise ValueError(f"duplicate or conflicting labels require adjudication for {eid}")
        chosen = dict(adjudicated[0] if adjudicated else records[0])
        if len(records) > 1:
            chosen["review_records"] = records
        resolved.append(chosen)
    return resolved


def _label_map(labels: list[dict]) -> dict[str, dict]:
    result = {}
    for label in labels:
        eid = str(label["episode_id"])
        if eid in result:
            raise ValueError("labels must have one resolved record per episode; call validate_labels first")
        result[eid] = label
    return result


def _episodes(predictions: list[dict]) -> dict[str, dict[str, dict]]:
    """Reduce stochastic replicates to episode scores and retain every failure."""
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    seen = set()
    for prediction in predictions:
        key = (str(prediction["condition"]), str(prediction["episode_id"]), prediction.get("replicate", 0))
        if key in seen:
            raise ValueError(f"duplicate prediction replicate: {key}")
        seen.add(key)
        buckets[key[:2]].append(prediction)
    result: dict[str, dict[str, dict]] = defaultdict(dict)
    for (condition, eid), records in buckets.items():
        groups = {str(p.get("group_id") or eid) for p in records}
        if len(groups) != 1:
            raise ValueError(f"inconsistent group_id for {eid}")
        scores, implementations = [], []
        for record in records:
            assessment = record.get("assessment")
            if record.get("status") == "ok" and isinstance(assessment, dict):
                score = assessment.get("implemented_collusion_probability")
                implementation = assessment.get("implementation", assessment.get("implementation_assessment"))
                if (
                    isinstance(score, (int, float))
                    and not isinstance(score, bool)
                    and math.isfinite(score)
                    and 0 <= score <= 1
                ) and implementation in ("supported", "not_supported", "insufficient_evidence"):
                    scores.append(float(score))
                    implementations.append(implementation)
        result[condition][eid] = {
            "episode_id": eid,
            "group_id": next(iter(groups)),
            "score": mean(scores) if scores else None,
            "valid_calls": len(scores),
            "error_calls": len(records) - len(scores),
            "insufficient_calls": implementations.count("insufficient_evidence"),
            "records": records,
        }
    return dict(result)


def calibrate(
    predictions: list[dict],
    labels: list[dict],
    splits: dict,
    target_fpr: float = 0.1,
    fixed_threshold: float | None = None,
) -> dict:
    """Freeze thresholds from development predictions only, using score >= threshold.

    Passing even one held-out prediction is an error. Labels outside development
    are ignored completely, including when computing the frozen label hash.
    """
    if not 0 <= target_fpr <= 1:
        raise ValueError("target_fpr must be between zero and one")
    if fixed_threshold is not None and (not math.isfinite(fixed_threshold) or not 0 <= fixed_threshold <= 1):
        raise ValueError("fixed_threshold must be between zero and one")
    if any(splits.get(p["episode_id"]) != "development" for p in predictions):
        raise ValueError("calibration accepts development predictions only; held-out/missing split is forbidden")
    development_labels = [label for label in labels if splits.get(label["episode_id"]) == "development"]
    truth = _label_map(development_labels)
    thresholds, diagnostics = {}, {}
    for condition, episodes in sorted(_episodes(predictions).items()):
        data = [
            (e["score"], truth[eid]["implemented_collusion"] == "positive")
            for eid, e in episodes.items()
            if e["score"] is not None
            and eid in truth
            and truth[eid].get("implemented_collusion") in ("positive", "negative")
        ]
        negatives = [score for score, positive in data if not positive]
        if fixed_threshold is None:
            if not negatives:
                raise ValueError(f"development negatives are required to calibrate {condition}")
            candidates = sorted({0.0, *(s for s, _ in data), *(math.nextafter(s, math.inf) for s, _ in data)})
            threshold = next(t for t in candidates if sum(s >= t for s in negatives) / len(negatives) <= target_fpr)
        else:
            threshold = float(fixed_threshold)
        thresholds[condition] = threshold
        diagnostics[condition] = {
            "n_binary": len(data),
            "n_negative": len(negatives),
            "actual_development_fpr": sum(s >= threshold for s in negatives) / len(negatives) if negatives else None,
        }
    return {
        "thresholds": thresholds,
        "comparison": ">=",
        "target_fpr": target_fpr,
        "method": "fixed" if fixed_threshold is not None else "development_fpr",
        "label_hash": _hash(sorted(development_labels, key=lambda x: x["episode_id"])),
        "split_hash": _hash(splits),
        "diagnostics": diagnostics,
    }


def _binary_metrics(rows: list[tuple[float, bool]], threshold: float) -> dict:
    tp = sum(score >= threshold and positive for score, positive in rows)
    fp = sum(score >= threshold and not positive for score, positive in rows)
    fn = sum(score < threshold and positive for score, positive in rows)
    tn = sum(score < threshold and not positive for score, positive in rows)
    positives, negatives = tp + fn, tn + fp
    recall = tp / positives if positives else None
    fpr = fp / negatives if negatives else None
    positive_scores = [s for s, p in rows if p]
    negative_scores = [s for s, p in rows if not p]
    auc = (
        (sum((a > b) + 0.5 * (a == b) for a in positive_scores for b in negative_scores) / (positives * negatives))
        if positives and negatives
        else None
    )
    return {
        "n": len(rows),
        "positive": positives,
        "negative": negatives,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "accuracy": (tp + tn) / len(rows) if rows else None,
        "recall": recall,
        "false_positive_rate": fpr,
        "precision": tp / (tp + fp) if tp + fp else None,
        "balanced_accuracy": (recall + 1 - fpr) / 2 if recall is not None and fpr is not None else None,
        "auroc": auc,
    }


def _usage(records: list[dict]) -> dict:
    """Summarize attributed deployment calls, keeping unknown metadata null."""
    calls = [call for record in records for call in record.get("calls", [])]
    aliases = {
        "tokens": ("total_tokens", "tokens"),
        "cost": ("cost", "billed_cost", "cost_usd", "billed_cost_usd"),
        "actual_cost": ("actual_billed_cost_usd", "cost_usd", "billed_cost_usd", "cost"),
        "latency": ("latency_seconds", "latency", "latency_s"),
        "actual_latency": ("actual_latency_seconds", "latency_seconds", "latency", "latency_s"),
    }
    result: dict[str, Any] = {"calls": len(calls)}
    for name, keys in aliases.items():
        values = []
        partial_values = []
        for call in calls:
            blocks = [call, call.get("metadata") or {}, call.get("usage") or {}]
            blocks += [block.get("usage") or {} for block in list(blocks)]
            value = next((block[k] for block in blocks for k in keys if block.get(k) is not None), None)
            if name == "tokens" and value is None:
                for block in blocks:
                    for input_key, output_key in (
                        ("prompt_tokens", "completion_tokens"),
                        ("input_tokens", "output_tokens"),
                    ):
                        if block.get(input_key) is not None and block.get(output_key) is not None:
                            value = block[input_key] + block[output_key]
                            break
                    if value is not None:
                        break
            attempts = call.get("attempts") or []
            # A successful last response does not make earlier transport-error
            # billing or token usage known. Cache hits made no fresh attempts.
            cached_actual = name == "actual_cost" and call.get("cached") is True
            if attempts and name in ("tokens", "cost", "actual_cost") and not cached_actual:
                attempt_values = []
                for attempt in attempts:
                    if name == "tokens":
                        incoming, outgoing = attempt.get("input_tokens"), attempt.get("output_tokens")
                        measured = incoming + outgoing if incoming is not None and outgoing is not None else None
                    else:
                        measured = attempt.get("cost_usd")
                    attempt_values.append(measured)
                known = [
                    float(v)
                    for v in attempt_values
                    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                ]
                if len(known) == len(attempt_values):
                    value = sum(known)
                else:
                    value = None
                    if known:
                        partial_values.append(sum(known))
            elif value is None and name in ("cost", "actual_cost") and call.get("known_cost_usd") is not None:
                partial_values.append(float(call["known_cost_usd"]))
            elif value is None and name == "tokens":
                known_input, known_output = call.get("known_input_tokens"), call.get("known_output_tokens")
                if known_input is not None or known_output is not None:
                    partial_values.append(float(known_input or 0) + float(known_output or 0))
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                values.append(float(value))
        result[name] = {
            "total": sum(values) if values and len(values) == len(calls) else None,
            "known_subtotal": sum(values) + sum(partial_values) if values or partial_values else None,
            "known_calls": len(values),
            "unknown_calls": len(calls) - len(values),
        }
    return result


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * q
    low, high = math.floor(index), math.ceil(index)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def _paired(
    left: dict, right: dict, truth: dict, left_threshold: float, right_threshold: float, samples: int, seed: int
) -> dict:
    ids = sorted(
        eid
        for eid in left.keys() & right.keys()
        if left[eid]["score"] is not None
        and right[eid]["score"] is not None
        and truth.get(eid, {}).get("implemented_collusion") in ("positive", "negative")
    )
    clusters: dict[str, list[str]] = defaultdict(list)
    for eid in ids:
        if left[eid]["group_id"] != right[eid]["group_id"]:
            raise ValueError(f"conditions disagree on group_id for {eid}")
        clusters[left[eid]["group_id"]].append(eid)

    def difference(chosen: list[str]) -> dict:
        a = _binary_metrics(
            [(left[eid]["score"], truth[eid]["implemented_collusion"] == "positive") for eid in chosen], left_threshold
        )
        b = _binary_metrics(
            [(right[eid]["score"], truth[eid]["implemented_collusion"] == "positive") for eid in chosen],
            right_threshold,
        )
        return {
            metric: a[metric] - b[metric] if a[metric] is not None and b[metric] is not None else None
            for metric in ("accuracy", "recall", "false_positive_rate", "balanced_accuracy")
        }

    point = difference(ids)
    draws: dict[str, list[float]] = defaultdict(list)
    rng = random.Random(seed)
    keys = sorted(clusters)
    if keys:
        for _ in range(samples):
            chosen = [eid for key in rng.choices(keys, k=len(keys)) for eid in clusters[key]]
            for metric, value in difference(chosen).items():
                if value is not None:
                    draws[metric].append(value)
    return {
        "n_paired_episodes": len(ids),
        "n_groups": len(keys),
        "episode_ids": ids,
        "direction": "left minus right",
        "bootstrap_samples": samples,
        "differences": {
            metric: {
                "estimate": value,
                "ci95": [_percentile(draws[metric], 0.025), _percentile(draws[metric], 0.975)],
                "valid_bootstrap_samples": len(draws[metric]),
            }
            for metric, value in point.items()
        },
    }


def evaluate(
    predictions: list[dict],
    labels: list[dict],
    splits: dict,
    thresholds: dict | float = 0.5,
    bootstrap_samples: int = 500,
    seed: int = 0,
    split: str = "held_out",
) -> dict:
    """Evaluate held-out episodes and paired group-bootstrap differences honestly."""
    if bootstrap_samples < 0:
        raise ValueError("bootstrap_samples cannot be negative")
    truth = _label_map(labels)
    if isinstance(thresholds, dict) and "thresholds" in thresholds:
        if thresholds.get("split_hash") and thresholds["split_hash"] != _hash(splits):
            raise ValueError("frozen threshold split hash does not match evaluation splits")
        development_labels = [label for label in labels if splits.get(label["episode_id"]) == "development"]
        if thresholds.get("label_hash") and thresholds["label_hash"] != _hash(
            sorted(development_labels, key=lambda x: x["episode_id"])
        ):
            raise ValueError("development labels changed after threshold freeze")
        threshold_map = thresholds["thresholds"]
    else:
        threshold_map = thresholds
    selected = [p for p in predictions if splits.get(p["episode_id"]) == split]
    conditions = _episodes(selected)
    by_condition, cutoffs = {}, {}
    for condition, episodes in sorted(conditions.items()):
        if isinstance(threshold_map, dict):
            if condition not in threshold_map:
                raise ValueError(f"missing frozen threshold for {condition}")
            cutoff = threshold_map[condition]
        else:
            cutoff = threshold_map
        if not isinstance(cutoff, (int, float)) or not math.isfinite(cutoff):
            raise ValueError("threshold must be a finite number")
        cutoffs[condition] = cutoff
        rows, exclusions = [], Counter()
        all_records = []
        for eid, episode in episodes.items():
            all_records.extend(episode["records"])
            label = truth.get(eid, {}).get("implemented_collusion")
            if label not in ("positive", "negative"):
                exclusions["ambiguous" if label == "ambiguous" else "unlabeled"] += 1
            elif episode["score"] is None:
                exclusions["error_only"] += 1
            else:
                rows.append((episode["score"], label == "positive"))
        valid = sum(e["valid_calls"] for e in episodes.values())
        errors = sum(e["error_calls"] for e in episodes.values())
        insufficient = sum(e["insufficient_calls"] for e in episodes.values())
        kinds = Counter()
        for episode in episodes.values():
            record = episode["records"][0]
            metadata = record.get("episode_metadata") or record.get("metadata") or {}
            synthetic = record.get("synthetic", metadata.get("synthetic"))
            kind = record.get("data_kind", metadata.get("data_kind"))
            kinds[
                "synthetic"
                if synthetic is True or kind == "synthetic"
                else "real"
                if synthetic is False or kind == "real"
                else "unknown"
            ] += 1
        by_condition[condition] = {
            **_binary_metrics(rows, cutoff),
            "threshold": cutoff,
            "total_episodes": len(episodes),
            "total_prediction_calls": valid + errors,
            "valid_prediction_calls": valid,
            "error_prediction_calls": errors,
            "error_only_episodes": sum(e["score"] is None for e in episodes.values()),
            "exclusions": {key: exclusions[key] for key in ("ambiguous", "unlabeled", "error_only")},
            "insufficient_evidence": {
                "count": insufficient,
                "denominator": valid,
                "frequency": insufficient / valid if valid else None,
            },
            "data_kinds": dict(kinds),
            "usage": _usage(all_records),
        }
    requested = [
        ("MAO_RAW", "M_RAW"),
        ("MAO_RAW", "A_RAW"),
        ("MAO_EAE", "MAO_RAW"),
        ("MAO_LINKED", "MAO_EAE"),
        ("MAO_INTERACTIVE", "MAO_LINKED"),
        ("MAO_INTERACTIVE", "MAO_EAE"),
        ("MAO_INTERACTIVE", "MAO_RAW"),
    ]
    requested += [("MAO_INTERACTIVE", name) for name in conditions if "CONTROL" in name or "REVIEW" in name]
    paired = {
        f"{left}__vs__{right}": {
            "left": left,
            "right": right,
            **_paired(
                conditions[left], conditions[right], truth, cutoffs[left], cutoffs[right], bootstrap_samples, seed
            ),
        }
        for left, right in requested
        if left in conditions and right in conditions and left != right
    }
    return {
        "split": split,
        "has_mocked_predictions": any(
            p.get("mocked") or any(c.get("mocked") for c in p.get("calls", [])) for p in selected
        ),
        "has_synthetic_episodes": any(p.get("synthetic") for p in selected),
        "threshold_rule": "score >= threshold",
        "replicate_aggregation": "mean valid episode score",
        "bootstrap_unit": "group_id",
        "seed": seed,
        "excluded_prediction_calls_outside_split": len(predictions) - len(selected),
        "by_condition": by_condition,
        "paired_comparisons": paired,
        "has_binary_labels": any(c["n"] > 0 for c in by_condition.values()),
        "limitations": [
            "Scores are rankings, not established calibrated probabilities.",
            "Synthetic fixtures and mocked calls test software behavior, not model capability.",
            "Small pilots are exploratory and cannot establish reliable 1% false-positive rates.",
            "Paired comparisons use only episodes with valid outputs and binary labels in both conditions.",
            "Bootstrap intervals are unstable with few independent groups; undefined resamples are excluded and counted.",
            "Deployment costs must attribute the initial report to both LINKED and INTERACTIVE, including cached reuse.",
        ],
    }


def write_report(metrics: dict, out_dir: Path) -> Path:
    """Write readable Markdown, machine metrics and optional labeled metric plots."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")

    def value(number: Any) -> str:
        return "unavailable" if number is None else f"{number:.3f}" if isinstance(number, float) else str(number)

    lines = [
        "# Collusion detection research report",
        "",
        f"Evaluation split: **{metrics.get('split', 'unknown')}**.",
        "",
        "These are research assessments, not legal determinations. Inspect provenance before interpreting results.",
        "",
    ]
    if metrics.get("has_mocked_predictions"):
        lines += [
            "**MOCKED INFERENCE: software verification only. Scores, token counts and latency do not measure hosted model capability or deployment usage.**",
            "",
        ]
    if metrics.get("has_synthetic_episodes"):
        lines += [
            "**SYNTHETIC DATA: fixture-author expectations are not human annotations of real market episodes.**",
            "",
        ]
    if not metrics.get("has_binary_labels"):
        lines += [
            "**No labeled benchmark accuracy is reported:** there are no usable binary labels for this split.",
            "",
        ]
    lines += [
        "| Condition | Binary N | TP / FP / FN / TN | Recall | FPR | Precision | Balanced accuracy | AUROC |",
        "|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for condition, data in metrics.get("by_condition", {}).items():
        confusion = " / ".join(str(data["confusion"][key]) for key in ("tp", "fp", "fn", "tn"))
        lines.append(
            f"| {condition} | {data['n']} | {confusion} | {value(data['recall'])} | "
            f"{value(data['false_positive_rate'])} | {value(data['precision'])} | "
            f"{value(data['balanced_accuracy'])} | {value(data['auroc'])} |"
        )
    lines += ["", "## Denominators, exclusions and deployment usage", ""]
    for condition, data in metrics.get("by_condition", {}).items():
        usage = data["usage"]
        lines += [
            (
                f"- **{condition}**: {data['total_episodes']} episodes; {data['valid_prediction_calls']} valid calls, "
                f"{data['error_prediction_calls']} errors; exclusions {json.dumps(data['exclusions'], sort_keys=True)}. "
                f"Insufficient evidence: {data['insufficient_evidence']['count']}/{data['insufficient_evidence']['denominator']}. "
                f"Provenance: {json.dumps(data['data_kinds'], sort_keys=True)}. "
                f"Deployment calls: {usage['calls']}; tokens {value(usage['tokens']['total'])}; "
                f"deployment cost USD {value(usage['cost']['total'])}; actual billed cost USD "
                f"{value(usage['actual_cost']['total'])}; summed deployment call latency seconds "
                f"{value(usage['latency']['total'])}; actual call latency seconds "
                f"{value(usage['actual_latency']['total'])}."
            )
        ]
    lines += [
        "",
        "Unknown metadata remains null, including partially observed totals. Known subtotals are in metrics.json.",
        "",
        "## Paired group bootstrap",
        "",
    ]
    for name, comparison in metrics.get("paired_comparisons", {}).items():
        lines += [
            f"- **{name}**: {comparison['n_paired_episodes']} paired episodes in {comparison['n_groups']} groups; left minus right."
        ]
        for metric, difference in comparison["differences"].items():
            low, high = difference["ci95"]
            lines.append(
                f"  - {metric}: {value(difference['estimate'])}; 95% interval "
                f"[{value(low)}, {value(high)}]; {difference['valid_bootstrap_samples']} defined resamples."
            )
    lines += ["", "## Interpretation limits", ""] + [f"- {text}" for text in metrics.get("limitations", [])]
    if metrics.get("has_binary_labels"):
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        conditions = list(metrics["by_condition"])
        fig, ax = plt.subplots(figsize=(max(7, len(conditions) * 0.9), 4.5))
        positions = list(range(len(conditions)))
        for offset, metric, label in (
            (-0.18, "recall", "Recall"),
            (0.18, "false_positive_rate", "False-positive rate"),
        ):
            valid_positions = [i for i in positions if metrics["by_condition"][conditions[i]][metric] is not None]
            ax.bar(
                [i + offset for i in valid_positions],
                [metrics["by_condition"][conditions[i]][metric] for i in valid_positions],
                width=0.36,
                label=label,
            )
        ax.set_xticks(positions, conditions, rotation=35, ha="right")
        ax.set_ylim(0, 1.05)
        ax.set_ylabel("Fraction on labeled episodes")
        ax.set_title(
            "MOCKED / SYNTHETIC — software checks only"
            if metrics.get("has_mocked_predictions") or metrics.get("has_synthetic_episodes")
            else "Exploratory detection metrics — inspect provenance"
        )
        ax.legend()
        fig.tight_layout()
        fig.savefig(out_dir / "detection_metrics.png", dpi=140)
        plt.close(fig)
        lines += ["", "![Exploratory metric plot](detection_metrics.png)"]
    path = out_dir / "report.md"
    path.write_text("\n".join(lines) + "\n")
    return path
