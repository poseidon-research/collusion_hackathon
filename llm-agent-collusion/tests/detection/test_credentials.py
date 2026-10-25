"""Credential setup tests use temporary dummy secrets and no network."""

import asyncio
from unittest.mock import AsyncMock, patch

from src.collusion_detection.cli import hosted_client, main
from src.collusion_detection.config import StudyConfig
from src.collusion_detection.credentials import load_live_credentials
from src.collusion_detection.fixtures import make_fixtures
from src.collusion_detection.providers import RoleConfig
from src.collusion_detection.safety import ROOT


def test_env_loads_only_configured_keys_without_interpolation(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    path = tmp_path / ".env"
    path.write_text("OPENROUTER_API_KEY=unit-${DO_NOT_EXPAND}\nNEBIUS_API_KEY=unit-unused\nLLM_MODEL=unwanted\n")
    load_live_credentials([RoleConfig(provider="openrouter", model="exact")], path)
    import os

    assert os.environ["OPENROUTER_API_KEY"] == "unit-${DO_NOT_EXPAND}"
    assert "NEBIUS_API_KEY" not in os.environ and "LLM_MODEL" not in os.environ


def test_environment_precedence_and_custom_provider_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "unit-existing")
    monkeypatch.delenv("DETECTION_TEST_KEY", raising=False)
    path = tmp_path / ".env"
    path.write_text("OPENROUTER_API_KEY=unit-file\nDETECTION_TEST_KEY=unit-custom\n")
    load_live_credentials(
        [
            RoleConfig(provider="openrouter", model="exact"),
            RoleConfig(provider="nebius", model="exact", api_key_env="DETECTION_TEST_KEY"),
        ],
        path,
    )
    import os

    assert os.environ["OPENROUTER_API_KEY"] == "unit-existing"
    assert os.environ["DETECTION_TEST_KEY"] == "unit-custom"


def test_live_client_entrypoint_loads_keys_before_construction():
    config = StudyConfig()
    for name in ["assistant", "judge"]:
        config.roles[name] = RoleConfig(provider="openrouter", model="unit-exact")
    events = []

    def loaded(roles):
        events.append("loaded")

    with (
        patch("src.collusion_detection.cli.load_live_credentials", side_effect=loaded) as loader,
        patch("src.collusion_detection.cli.LiveClient") as client,
    ):
        client.side_effect = lambda *args, **kwargs: (
            events.append("constructed")
            or type(
                "InjectedClient",
                (),
                {
                    "validate_models": AsyncMock(
                        return_value=[
                            {
                                "context_limit_manually_configured": False,
                                "capabilities_manually_verified_required": False,
                            }
                        ]
                        * 2
                    )
                },
            )()
        )
        asyncio.run(hosted_client(config, ["assistant", "judge"]))
    assert events == ["loaded", "constructed"]
    loader.assert_called_once()


def test_offline_and_live_dry_run_never_load_env(tmp_path):
    make_fixtures(tmp_path / "fixtures")
    source = str(tmp_path / "fixtures/episodes.jsonl")
    with (
        patch("src.collusion_detection.cli.load_live_credentials", side_effect=AssertionError("Read .env offline")),
        patch(
            "src.collusion_detection.credentials.dotenv_values", side_effect=AssertionError("Read local credentials")
        ),
    ):
        main(
            [
                "run",
                "--config",
                str(ROOT / "configs/detection/offline.json"),
                "--episodes",
                source,
                "--out",
                str(tmp_path / "run"),
            ]
        )
        main(
            [
                "run",
                "--live",
                "--dry-run",
                "--config",
                str(ROOT / "configs/detection/live-smoke.json"),
                "--episodes",
                source,
                "--out",
                str(tmp_path / "plan.json"),
            ]
        )
