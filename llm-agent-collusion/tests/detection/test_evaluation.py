"""Known-data acceptance tests for private labels and paired evaluation."""

import math

import pytest

from src.collusion_detection.evaluation import (
    assign_splits,
    calibrate,
    evaluate,
    validate_labels,
    write_report,
)


def prediction(eid, score, condition="MAO_RAW", *, replicate=0, group=None, status="ok", implementation="supported"):
    return {
        "episode_id": eid,
        "group_id": group or eid,
        "condition": condition,
        "replicate": replicate,
        "status": status,
        "synthetic": True,
        "assessment": {"implemented_collusion_probability": score, "implementation": implementation}
        if status == "ok"
        else None,
        "calls": [{"metadata": {"usage": {"total_tokens": 20}, "cost": 0.01, "latency_seconds": 0.25}}],
    }


def label(eid, decision, *, reviewer="fixture-author", **kwargs):
    return {
        "episode_id": eid,
        "reviewer": reviewer,
        "agreement": "supported",
        "implemented_collusion": decision,
        "coalition": [],
        "evidence_ids": [],
        "economic_consequences": None,
        "adjudicator": None,
        **kwargs,
    }


def test_splits_keep_group_and_branch_connected_and_are_order_independent():
    episodes = [
        {"episode_id": "a", "group_id": "same"},
        {"episode_id": "b", "group_id": "same"},
        {"episode_id": "branch", "group_id": "branch", "parent_episode_id": "a"},
        {"episode_id": "other", "group_id": "other"},
        {"episode_id": "last", "group_id": "last"},
    ]
    splits = assign_splits(episodes, seed=42)
    assert splits["a"] == splits["b"] == splits["branch"]
    assert set(splits.values()) == {"development", "held_out"}
    assert splits == assign_splits(list(reversed(episodes)), seed=42)


def test_labels_validate_references_reviewers_and_adjudication():
    episodes = [{"episode_id": "one", "participants": ["s1", "s2"], "events": [{"event_id": "e1"}]}]
    positive = label("one", "positive", evidence_ids=["e1"], coalition=["s1", "s2"])
    assert validate_labels([positive], episodes)[0]["implemented_collusion"] == "positive"
    for changed in (
        dict(positive, evidence_ids=["fake"]),
        dict(positive, coalition=["fake"]),
        dict(positive, reviewer=""),
    ):
        with pytest.raises(ValueError):
            validate_labels([changed], episodes)
    with pytest.raises(ValueError, match="adjudication"):
        validate_labels([positive, label("one", "negative", reviewer="other")], episodes)
    with pytest.raises(ValueError, match="adjudication"):
        validate_labels([positive, positive], episodes)
    resolved = validate_labels(
        [
            positive,
            label("one", "negative", reviewer="other"),
            label("one", "positive", reviewer="adjudicator", adjudicator="reviewer-three"),
        ],
        episodes,
    )
    assert resolved[0]["implemented_collusion"] == "positive"
    assert len(resolved[0]["review_records"]) == 3


def test_calibration_rejects_heldout_even_errors_and_ignores_heldout_labels():
    splits = {"dev-negative": "development", "heldout": "held_out"}
    predictions = [prediction("dev-negative", 0.7)]
    labels = [label("dev-negative", "negative"), label("heldout", "positive")]
    result = calibrate(predictions, labels, splits, target_fpr=0)
    assert result["thresholds"]["MAO_RAW"] == math.nextafter(0.7, math.inf)
    assert result["diagnostics"]["MAO_RAW"]["actual_development_fpr"] == 0
    assert (
        result["label_hash"]
        == calibrate(predictions, [labels[0], label("heldout", "negative")], splits, target_fpr=0)["label_hash"]
    )
    with pytest.raises(ValueError, match="development"):
        calibrate(predictions + [prediction("heldout", None, status="error")], labels, splits)


def test_gold_coalition_cannot_include_buyer():
    episodes = [
        {
            "episode_id": "e",
            "participants": [{"alias": "S01", "role": "seller"}, {"alias": "B01", "role": "buyer"}],
            "events": [],
        }
    ]
    with pytest.raises(ValueError, match="nonseller"):
        validate_labels([label("e", "positive", coalition=["S01", "B01"])], episodes)


def test_calibration_ties_and_one_score_can_predict_none():
    labels = [label("n", "negative"), label("p", "positive")]
    predictions = [prediction("n", 1), prediction("p", 1)]
    frozen = calibrate(predictions, labels, {"n": "development", "p": "development"}, target_fpr=0)
    assert frozen["thresholds"]["MAO_RAW"] > 1
    assert frozen["diagnostics"]["MAO_RAW"]["actual_development_fpr"] == 0


def test_known_confusion_auroc_and_replicates_are_not_independent():
    labels = [label("p1", "positive"), label("p2", "positive"), label("n1", "negative"), label("n2", "negative")]
    predictions = [
        prediction("p1", 0.8),
        prediction("p1", 1.0, replicate=1),
        prediction("p2", 0.4),
        prediction("n1", 0.7),
        prediction("n2", 0.1),
    ]
    splits = {record["episode_id"]: "held_out" for record in labels}
    metrics = evaluate(predictions, labels, splits)["by_condition"]["MAO_RAW"]
    assert metrics["n"] == 4
    assert metrics["total_prediction_calls"] == 5
    assert metrics["confusion"] == {"tp": 1, "fp": 1, "fn": 1, "tn": 1}
    assert metrics["accuracy"] == metrics["recall"] == metrics["false_positive_rate"] == 0.5
    assert metrics["precision"] == metrics["balanced_accuracy"] == 0.5
    assert metrics["auroc"] == 0.75
    assert metrics["usage"]["tokens"]["total"] == 100
    assert metrics["usage"]["cost"]["total"] == 0.05


def test_errors_ambiguous_unlabeled_insufficient_and_unknown_usage():
    predictions = [
        prediction("p", 0.7, implementation="insufficient_evidence"),
        prediction("bad", None, status="error"),
        prediction("ambiguous", 0.9),
        prediction("unlabeled", 0.3),
    ]
    predictions[0]["calls"] = [{}]
    labels = [label("p", "positive"), label("bad", "negative"), label("ambiguous", "ambiguous")]
    metrics = evaluate(predictions, labels, {p["episode_id"]: "held_out" for p in predictions})["by_condition"][
        "MAO_RAW"
    ]
    assert metrics["n"] == 1
    assert metrics["error_prediction_calls"] == 1
    assert metrics["exclusions"] == {"error_only": 1, "ambiguous": 1, "unlabeled": 1}
    assert metrics["insufficient_evidence"] == {"count": 1, "denominator": 3, "frequency": 1 / 3}
    assert metrics["usage"]["cost"]["total"] is None
    assert metrics["usage"]["cost"]["known_subtotal"] == 0.03
    assert metrics["usage"]["cost"]["unknown_calls"] == 1


def test_invalid_output_stays_error_not_negative():
    p = prediction("p", 2)
    metrics = evaluate([p], [label("p", "positive")], {"p": "held_out"})["by_condition"]["MAO_RAW"]
    assert metrics["n"] == 0
    assert metrics["confusion"]["fn"] == 0
    assert metrics["error_prediction_calls"] == 1


def test_cached_reports_preserve_deployment_cost_but_actual_bill_is_zero():
    p = prediction("p", 0.9)
    p["calls"] = [
        {
            "input_tokens": 30,
            "output_tokens": 10,
            "cost_usd": 0.02,
            "actual_billed_cost_usd": 0.0,
            "latency_seconds": 0.1,
            "actual_latency_seconds": 0.0,
            "cached": True,
        }
    ]
    usage = evaluate([p], [label("p", "positive")], {"p": "held_out"})["by_condition"]["MAO_RAW"]["usage"]
    assert usage["cost"]["total"] == 0.02
    assert usage["actual_cost"]["total"] == 0
    assert usage["tokens"]["total"] == 40
    assert usage["latency"]["total"] == 0.1
    assert usage["actual_latency"]["total"] == 0


def test_retry_unknown_usage_preserves_known_subtotal_without_claiming_total():
    p = prediction("p", 0.9)
    p["calls"] = [
        {
            "input_tokens": None,
            "output_tokens": None,
            "cost_usd": None,
            "known_cost_usd": 0.02,
            "known_input_tokens": 30,
            "known_output_tokens": 10,
            "actual_billed_cost_usd": None,
            "attempts": [
                {"cost_usd": None, "input_tokens": None, "output_tokens": None},
                {"cost_usd": 0.02, "input_tokens": 30, "output_tokens": 10},
            ],
        }
    ]
    usage = evaluate([p], [label("p", "positive")], {"p": "held_out"})["by_condition"]["MAO_RAW"]["usage"]
    assert usage["cost"]["total"] is None
    assert usage["cost"]["known_subtotal"] == 0.02
    assert usage["tokens"]["total"] is None
    assert usage["tokens"]["known_subtotal"] == 40
    assert usage["actual_cost"]["total"] is None
    assert usage["actual_cost"]["known_subtotal"] == 0.02
    p["calls"][0].update(cached=True, actual_billed_cost_usd=0.0)
    cached = evaluate([p], [label("p", "positive")], {"p": "held_out"})["by_condition"]["MAO_RAW"]["usage"]
    assert cached["actual_cost"]["total"] == 0
    assert cached["cost"]["total"] is None
    assert cached["cost"]["known_subtotal"] == 0.02


def test_paired_intersection_and_group_bootstrap_known_difference():
    labels = [label("p1", "positive"), label("p2", "positive"), label("n1", "negative"), label("n2", "negative")]
    predictions = []
    for item in labels:
        positive = item["implemented_collusion"] == "positive"
        group = "pair-a" if item["episode_id"] in ("p1", "n1") else "pair-b"
        predictions += [
            prediction(item["episode_id"], 0.9 if positive else 0.1, "MAO_RAW", group=group),
            prediction(item["episode_id"], 0.1 if positive else 0.9, "M_RAW", group=group),
        ]
    predictions += [prediction("unpaired", 0.2)]
    splits = {p["episode_id"]: "held_out" for p in predictions}
    metrics = evaluate(predictions, labels, splits, bootstrap_samples=100, seed=3)
    comparison = metrics["paired_comparisons"]["MAO_RAW__vs__M_RAW"]
    assert comparison["n_paired_episodes"] == 4
    assert comparison["n_groups"] == 2
    assert comparison["differences"]["accuracy"]["estimate"] == 1
    assert comparison["differences"]["accuracy"]["ci95"] == [1, 1]
    assert comparison["differences"]["recall"]["ci95"] == [1, 1]
    assert comparison["differences"]["false_positive_rate"]["ci95"] == [-1, -1]
    assert metrics == evaluate(predictions, labels, splits, bootstrap_samples=100, seed=3)


def test_freeze_hashes_checked_but_heldout_labels_may_be_added():
    labels = [label("dev", "negative")]
    splits = {"dev": "development", "held": "held_out"}
    frozen = calibrate([prediction("dev", 0.2)], labels, splits)
    evaluate([prediction("held", 0.1)], labels + [label("held", "negative")], splits, frozen)
    with pytest.raises(ValueError, match="labels changed"):
        evaluate([], [label("dev", "positive")], splits, frozen)
    with pytest.raises(ValueError, match="split hash"):
        evaluate([], labels, {"dev": "held_out", "held": "held_out"}, frozen)


def test_unlabeled_report_never_claims_benchmark_accuracy(tmp_path):
    metrics = evaluate([prediction("unlabeled", 0.8)], [], {"unlabeled": "held_out"})
    assert metrics["has_binary_labels"] is False
    assert metrics["by_condition"]["MAO_RAW"]["accuracy"] is None
    path = write_report(metrics, tmp_path)
    assert "No labeled benchmark accuracy is reported" in path.read_text()
    assert not (tmp_path / "detection_metrics.png").exists()


def test_labeled_report_writes_plot_and_metrics(tmp_path):
    pytest.importorskip("matplotlib")
    metrics = evaluate([prediction("positive", 0.8)], [label("positive", "positive")], {"positive": "held_out"})
    path = write_report(metrics, tmp_path)
    assert path.is_file()
    assert (tmp_path / "metrics.json").is_file()
    assert (tmp_path / "detection_metrics.png").is_file()
    assert "Synthetic fixtures and mocked calls" in path.read_text()


def test_reports_mark_mocked_outputs_on_real_source(tmp_path):
    row = prediction("real-source", 0.5)
    row["synthetic"] = False
    row["mocked"] = True
    metrics = evaluate([row], [], {"real-source": "held_out"}, bootstrap_samples=0)
    assert metrics["has_mocked_predictions"] and not metrics["has_synthetic_episodes"]
    path = write_report(metrics, tmp_path)
    assert "MOCKED INFERENCE" in path.read_text()
    assert "No labeled benchmark accuracy" in path.read_text()
