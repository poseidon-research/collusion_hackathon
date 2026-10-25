import asyncio
import json

from src.collusion_detection.cli import run_predictions
from src.collusion_detection.config import StudyConfig
from src.collusion_detection.fixtures import make_fixtures
from src.collusion_detection.providers import FakeClient


def test_episode_workers_use_shared_limit_and_keep_output_order(tmp_path):
    class ObservedFake(FakeClient):
        def __init__(self):
            super().__init__()
            self.active = 0
            self.peak = 0

        async def complete(self, *args, **kwargs):
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep(0.01)
                return await super().complete(*args, **kwargs)
            finally:
                self.active -= 1

    episodes = make_fixtures(tmp_path / "fixtures")
    client = ObservedFake()
    config = StudyConfig(conditions=["M_RAW", "MAO_LINKED", "MAO_INTERACTIVE"], concurrency=3)
    rows, _ = asyncio.run(run_predictions(config, episodes, tmp_path / "run", client))
    assert 1 < client.peak <= 3
    assert len(rows) == 18 and all(p["status"] == "ok" for p in rows)
    assert [(p["episode_id"], p["condition"]) for p in rows] == [
        (ep.episode_id, c) for ep in episodes for c in config.conditions
    ]
    progress = json.loads((tmp_path / "run/progress.json").read_text())
    assert progress["status"] == "complete" and progress["completed_episodes"] == 6 and progress["errors"] == 0
    for ep in episodes:
        linked = next(p for p in rows if p["episode_id"] == ep.episode_id and p["condition"] == "MAO_LINKED")
        interactive = next(p for p in rows if p["episode_id"] == ep.episode_id and p["condition"] == "MAO_INTERACTIVE")
        assert linked["initial_report_hash"] == interactive["initial_report_hash"]
        assert interactive["calls"][0]["cached"]


def test_parallel_error_usage_cannot_include_other_episode_calls(tmp_path):
    client = FakeClient(scripted=["invalid JSON"])
    config = StudyConfig(conditions=["M_RAW"], concurrency=3)
    episodes = make_fixtures(tmp_path / "fixtures")
    rows, _ = asyncio.run(run_predictions(config, episodes, tmp_path / "run", client))
    assert sum(p["status"] == "error" for p in rows) == 1
    assert all(len(p["calls"]) == 1 for p in rows)
    assert all(p["calls"][0]["namespace"].startswith(p["episode_id"] + "/") for p in rows)
