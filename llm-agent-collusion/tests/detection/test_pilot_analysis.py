"""Descriptive pilot summaries preserve missingness and incomplete coverage."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "docs/detection/analyze_pilot.py"


def load_analysis():
    spec = importlib.util.spec_from_file_location("pilot_analysis", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_actual_attempt_ledger_unknown_billing_is_not_zero():
    result = load_analysis().summarize_ledger(
        [
            {"cost_usd": 0.02, "input_tokens": 30, "output_tokens": 10, "latency_seconds": 0.2},
            {"cost_usd": None, "input_tokens": None, "output_tokens": None, "latency_seconds": 0.3},
        ]
    )
    assert result["attempts"] == 2
    assert result["billed_cost_usd"]["total"] is None
    assert result["billed_cost_usd"]["known_subtotal"] == 0.02
    assert result["billed_cost_usd"]["unknown_attempts"] == 1
    assert result["tokens"]["total"] is None
    assert result["tokens"]["known_subtotal"] == 40
    assert result["latency_seconds"]["total"] == 0.5


def test_partial_condition_coverage_is_explicitly_incomplete():
    result = load_analysis().coverage(
        [{"episode_id": "one", "condition": "M_RAW", "replicate": 0}],
        ["one", "two"],
        ["M_RAW"],
        1,
    )
    assert result["status"] == "incomplete"
    assert result["expected_predictions"] == 2
    assert result["observed_predictions"] == 1
    assert result["missing_predictions"] == [{"episode_id": "two", "condition": "M_RAW", "replicate": 0}]
