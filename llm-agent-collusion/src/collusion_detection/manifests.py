"""Bind calibration/evaluation to frozen provider, prompt and threshold choices."""

import json
from pathlib import Path

from .storage import digest, read_jsonl


def study_hash(config, prompts):
    values = config.model_dump() if hasattr(config, "model_dump") else dict(config)
    values = dict(values)
    values.pop("budget_usd", None)  # Spending allocation may differ; research/model settings cannot.
    return digest({"config": values, "prompts": prompts})


def verify_predictions(path, splits=None, thresholds=None):
    path = Path(path)
    manifest_path = path.parent / "manifest.json"
    if not manifest_path.exists():
        raise ValueError("CLI calibration/evaluation requires predictions beside their frozen manifest.json")
    manifest = json.loads(manifest_path.read_text())
    expected = manifest.get("manifest_hash")
    body = {k: v for k, v in manifest.items() if k != "manifest_hash"}
    if digest(body) != expected:
        raise ValueError("Frozen manifest hash mismatch")
    predictions = read_jsonl(path)
    conditions = set(manifest["config"]["conditions"])
    for prediction in predictions:
        if prediction.get("manifest_hash") != expected:
            raise ValueError("Mixed or unbound prediction manifest")
        eid, condition = prediction["episode_id"], prediction["condition"]
        if eid not in manifest["episode_ids"] or condition not in conditions:
            raise ValueError("Prediction is outside frozen episodes/conditions")
        view = condition.split("_", 1)[0]
        if prediction.get("evidence_hash") != manifest["evidence_hashes"][eid][view]:
            raise ValueError("Prediction evidence hash differs from frozen source view")
    if splits is not None:
        if manifest.get("splits") is None:
            raise ValueError("Freeze --splits during run before calibration/evaluation")
        if splits != manifest["splits"]:
            raise ValueError("Split map differs from frozen run")
    if thresholds is not None:
        if isinstance(thresholds, dict) and thresholds.get("study_hash") != manifest.get("study_hash"):
            raise ValueError("Threshold artifact must be bound to this provider/prompt/config study")
        configured = manifest["thresholds"]
        supplied = thresholds.get("thresholds", thresholds) if isinstance(thresholds, dict) else thresholds
        frozen = configured.get("thresholds", configured) if isinstance(configured, dict) else configured
        supplied_map = {c: supplied[c] if isinstance(supplied, dict) else supplied for c in conditions}
        frozen_map = {c: frozen[c] if isinstance(frozen, dict) else frozen for c in conditions}
        if supplied_map != frozen_map:
            raise ValueError("Evaluation thresholds differ from those frozen before this run")
    return predictions, manifest
