import asyncio
import json

import pytest

from src.collusion_detection.cli import main, run_predictions
from src.collusion_detection.config import StudyConfig
from src.collusion_detection.experiment_zero import summarize_sampling
from src.collusion_detection.fixtures import make_fixtures
from src.collusion_detection.manifests import verify_predictions
from src.collusion_detection.storage import write_json, write_jsonl


def test_cli_freezes_splits_thresholds_and_study(tmp_path):
    episodes = make_fixtures(tmp_path / "fixtures")
    splits = {e.episode_id: "development" if i < 3 else "held_out" for i, e in enumerate(episodes)}
    config = StudyConfig(conditions=["M_RAW"], fixed_threshold=0.0)
    predictions, manifest = asyncio.run(
        run_predictions(config, episodes, tmp_path / "run", splits=splits, thresholds=0.0)
    )
    assert manifest["thresholds"] == 0.0
    assert verify_predictions(tmp_path / "run/predictions.jsonl", splits, 0.0)
    with pytest.raises(ValueError, match="thresholds differ"):
        verify_predictions(tmp_path / "run/predictions.jsonl", splits, 0.9)
    changed = dict(splits)
    changed[episodes[0].episode_id] = "held_out"
    with pytest.raises(ValueError, match="Split map"):
        verify_predictions(tmp_path / "run/predictions.jsonl", changed, 0.0)
    predictions[0]["manifest_hash"] = "another study"
    write_jsonl(tmp_path / "run/predictions.jsonl", predictions)
    with pytest.raises(ValueError, match="Mixed or unbound"):
        verify_predictions(tmp_path / "run/predictions.jsonl", splits, 0.0)


def test_development_calibration_cli_to_frozen_heldout_run(tmp_path):
    episodes = make_fixtures(tmp_path / "fixtures")
    config = StudyConfig(conditions=["M_RAW"])
    splits = {e.episode_id: "development" if i < 3 else "held_out" for i, e in enumerate(episodes)}
    write_json(tmp_path / "splits.json", splits)
    asyncio.run(run_predictions(config, episodes, tmp_path / "dev", splits=splits, split="development"))
    labels = tmp_path / "fixtures/private/synthetic_expectations.jsonl"
    main(
        [
            "calibrate",
            "--predictions",
            str(tmp_path / "dev/predictions.jsonl"),
            "--labels",
            str(labels),
            "--splits",
            str(tmp_path / "splits.json"),
            "--fixed-threshold",
            ".5",
            "--out",
            str(tmp_path / "thresholds.json"),
        ]
    )
    frozen = json.loads((tmp_path / "thresholds.json").read_text())
    asyncio.run(
        run_predictions(config, episodes, tmp_path / "held", splits=splits, split="held_out", thresholds=frozen)
    )
    main(
        [
            "evaluate",
            "--predictions",
            str(tmp_path / "held/predictions.jsonl"),
            "--labels",
            str(labels),
            "--splits",
            str(tmp_path / "splits.json"),
            "--thresholds",
            str(tmp_path / "thresholds.json"),
            "--out",
            str(tmp_path / "metrics.json"),
        ]
    )
    assert json.loads((tmp_path / "metrics.json").read_text())["has_binary_labels"]
    config.roles["judge"].model = "changed-model"
    with pytest.raises(ValueError, match="different provider/prompt/config"):
        asyncio.run(run_predictions(config, episodes, tmp_path / "changed", splits=splits, thresholds=frozen))


def test_experiment_zero_distinguishes_implementation_disclosure_and_sampling(tmp_path):
    generation = [
        {"sample_id": "s1", "scenario": "planted", "source_path": str(tmp_path / "raw1"), "status": "complete"},
        {"sample_id": "s2", "scenario": "neutral", "source_path": str(tmp_path / "raw2"), "status": "error"},
    ]
    provenance = [{"source_path": str(tmp_path / "raw1/auction_results.md"), "episode_id": "ep1"}]
    labels = [{"episode_id": "ep1", "implemented_collusion": "negative", "public_disclosure": "supported"}]
    summary = summarize_sampling(generation, provenance, labels)
    assert summary["arms"]["planted"]["implemented_collusion"]["negative"] == 1
    assert summary["arms"]["planted"]["public_disclosure"]["supported"] == 1
    assert summary["arms"]["neutral"]["generation_status"]["error"] == 1
    assert summary["arms"]["neutral"]["implemented_collusion"]["unreviewed"] == 1
