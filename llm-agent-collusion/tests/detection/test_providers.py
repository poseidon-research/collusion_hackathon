"""Provider acceptance checks run with network disabled; HTTP is injected."""

import asyncio
import io
import json
from unittest.mock import patch

import pytest

from src.collusion_detection.providers import (
    BudgetExceeded,
    CallManager,
    FakeClient,
    LiveClient,
    ModelValidationError,
    ProviderError,
    ProviderResponse,
    RoleConfig,
    estimate_reservation,
)


def run(coroutine):
    return asyncio.run(coroutine)


def messages():
    return [
        {
            "role": "user",
            "content": json.dumps(
                {
                    "events": [
                        {"event_id": "e-1", "event_type": "message", "text": "Keep prices competitive."},
                    ]
                }
            ),
        }
    ]


def hosted_role(**overrides):
    return RoleConfig(
        provider="openrouter",
        model="vendor/exact",
        input_per_million=1.0,
        output_per_million=2.0,
        max_output_tokens=100,
        **overrides,
    )


def test_offline_never_reads_environment(tmp_path):
    with patch("src.collusion_detection.providers.os") as environment:
        environment.environ.get.side_effect = AssertionError("Offline credential/environment read")
        result = run(CallManager(FakeClient(), tmp_path).call(RoleConfig(), messages(), "report"))
    assert json.loads(result.text)["support"][0] == {"event_id": "e-1", "quote": "Keep prices competitive."}
    assert result.record["mocked"] is True


def test_fake_deterministic_and_scriptable(tmp_path):
    first = run(FakeClient().complete(RoleConfig(), messages(), "final"))
    second = run(FakeClient().complete(RoleConfig(), messages(), "final"))
    assert first.text == second.text
    assert json.loads(first.text)["implemented_collusion_probability"] == 0.5
    client = FakeClient(scripted=[{"custom": "value"}, "invalid JSON"])
    assert json.loads(run(client.complete(RoleConfig(), messages(), "final")).text) == {"custom": "value"}
    assert run(client.complete(RoleConfig(), messages(), "final")).text == "invalid JSON"


def test_fake_interactive_is_conversation_local():
    client = FakeClient()
    initial = [{"role": "system", "content": '{"action":{"enum":["ASK","FINISH"]}}'}] + messages()
    first = run(client.complete(RoleConfig(), initial, "decision"))
    assert json.loads(first.text)["action"] == "ASK"
    exchange = messages() + [
        {"role": "assistant", "content": first.text},
        {"role": "user", "content": '{"answer":"Here are source excerpts."}'},
    ]
    second = run(client.complete(RoleConfig(), exchange, "decision"))
    assert json.loads(second.text)["action"] == "FINISH"
    reset = run(client.complete(RoleConfig(), messages(), "decision"))
    assert json.loads(reset.text)["action"] == "ASK"


def test_cache_persists_and_replicates_do_not_collide(tmp_path):
    client = FakeClient()
    first_manager = CallManager(client, tmp_path)
    first = run(first_manager.call(RoleConfig(), messages(), "final"))
    resumed = CallManager(client, tmp_path)
    cached = run(resumed.call(RoleConfig(), messages(), "final"))
    different = run(resumed.call(RoleConfig(), messages(), "final", replicate=1))
    assert len(client.calls) == 2
    assert first.text == cached.text
    assert cached.record["cached"]
    assert different.record["cache_key"] != first.record["cache_key"]
    assert cached.record["request"]["messages"] == messages()
    assert len((tmp_path / "calls.jsonl").read_text().splitlines()) == 3


def test_concurrent_identical_calls_are_coalesced(tmp_path):
    client = FakeClient()

    async def scenario():
        manager = CallManager(client, tmp_path)
        return await asyncio.gather(*(manager.call(RoleConfig(), messages(), "final") for _ in range(7)))

    results = run(scenario())
    assert len(client.calls) == 1
    assert sum(not result.record["cached"] for result in results) == 1


def test_global_concurrency_across_managers(tmp_path):
    class SlowFake(FakeClient):
        async def complete(self, role, messages, kind):
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            try:
                await asyncio.sleep(0.01)
                return ProviderResponse("{}", returned_model=role.model)
            finally:
                self.in_flight -= 1

    client = SlowFake()

    async def scenario():
        managers = [CallManager(client, tmp_path / str(i)) for i in range(3)]
        await asyncio.gather(*(managers[i % 3].call(RoleConfig(), messages(), "final", replicate=i) for i in range(12)))

    run(scenario())
    assert client.max_in_flight == 3


def test_retry_failure_retains_unknown_usage_and_cost(tmp_path):
    client = FakeClient([ProviderError("throttled", retryable=True, retry_after=0), {"ok": True}])
    result = run(CallManager(client, tmp_path).call(RoleConfig(), messages(), "final"))
    assert len(client.calls) == 2
    failed, succeeded = result.record["attempts"]
    assert failed["cost_usd"] is None
    assert failed["input_tokens"] is None
    assert failed["usage_unknown"]
    assert succeeded["error"] is None
    assert result.record["input_tokens"] is None
    assert result.record["output_tokens"] is None
    assert result.record["cost_usd"] is None
    assert result.record["actual_billed_cost_usd"] is None
    assert result.record["known_input_tokens"] == succeeded["input_tokens"]
    assert result.record["known_output_tokens"] == succeeded["output_tokens"]
    assert result.record["unknown_cost_usd_attempts"] == 1
    assert result.record["last_response_usage"]["cost_usd"] == 0.0


def test_failed_retry_success_keeps_unknown_total_but_positive_reservation(tmp_path):
    role = hosted_role()
    response = ProviderResponse("{}", input_tokens=10, output_tokens=2, billed_cost_usd=0.00002)
    client = FakeClient([ProviderError("timeout", retryable=True, retry_after=0), response])
    manager = CallManager(client, tmp_path, budget_usd=2)
    result = run(manager.call(role, messages(), "final"))
    assert result.record["cost_usd"] is None
    assert result.record["input_tokens"] is None
    assert result.record["output_tokens"] is None
    assert result.record["known_cost_usd"] == 0.00002
    assert result.record["known_input_tokens"] == 10
    assert result.record["known_output_tokens"] == 2
    assert result.record["accounted_cost_usd"] == 2 * estimate_reservation(role, messages())
    assert manager.accounted_usd == result.record["accounted_cost_usd"] > 0
    cached = run(manager.call(role, messages(), "final"))
    assert cached.record["cost_usd"] is None
    assert cached.record["actual_billed_cost_usd"] == 0.0
    assert cached.record["last_response_usage"]["input_tokens"] == 10


def test_errors_are_not_cached_or_negative_predictions(tmp_path):
    client = FakeClient([ProviderError("fatal")])
    manager = CallManager(client, tmp_path)
    with pytest.raises(ProviderError, match="fatal"):
        run(manager.call(RoleConfig(), messages(), "final"))
    assert manager.records[-1]["status"] == "error"
    assert manager.records[-1]["cost_usd"] is None
    assert not list(tmp_path.glob("*.json"))


def test_retry_after_too_long_does_not_retry_early(tmp_path):
    client = FakeClient([ProviderError("throttled", retryable=True, retry_after=100)])
    with pytest.raises(ProviderError, match="bounded retry window"):
        run(CallManager(client, tmp_path).call(RoleConfig(), messages(), "final"))
    assert len(client.calls) == 1


def test_budget_charges_failed_attempts_and_survives_resume(tmp_path):
    role = hosted_role()
    reservation = estimate_reservation(role, messages())
    client = FakeClient([ProviderError("timeout", retryable=True, retry_after=0), {"ok": True}])
    manager = CallManager(client, tmp_path, budget_usd=reservation * 1.5)
    with pytest.raises(BudgetExceeded):
        run(manager.call(role, messages(), "final"))
    assert len(client.calls) == 1
    assert manager.accounted_usd == reservation
    assert manager.records[-1]["attempts"][0]["accounted_cost_usd"] == reservation
    resumed = CallManager(FakeClient(), tmp_path, budget_usd=reservation * 1.5)
    with pytest.raises(BudgetExceeded):
        run(resumed.call(role, messages(), "final"))


def test_live_budget_missing_prices_or_budget_is_rejected(tmp_path):
    client = FakeClient()
    with pytest.raises(BudgetExceeded):
        run(CallManager(client, tmp_path).call(hosted_role(), messages(), "final"))
    with pytest.raises(BudgetExceeded):
        run(
            CallManager(client, tmp_path, budget_usd=2).call(
                RoleConfig(provider="nebius", model="exact"), messages(), "final"
            )
        )
    assert not client.calls


def test_unknown_success_usage_remains_none(tmp_path):
    role = hosted_role()
    client = FakeClient([ProviderResponse("{}", returned_model=role.model)])
    result = run(CallManager(client, tmp_path, budget_usd=2).call(role, messages(), "final"))
    assert result.record["input_tokens"] is None
    assert result.record["output_tokens"] is None
    assert result.record["cost_usd"] is None
    assert result.record["accounted_cost_usd"] > 0


def test_live_authorization_and_global_override(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "unit-test-secret")
    with pytest.raises(ProviderError, match="explicit"):
        LiveClient([hosted_role()])
    monkeypatch.setenv("LLM_MODEL", "unintended-global-model")
    with pytest.raises(ProviderError, match="LLM_MODEL"):
        LiveClient([hosted_role()], explicit_live=True)


def test_live_catalog_no_completions_and_metadata(monkeypatch, tmp_path):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "unit-test-secret")
    role = hosted_role()
    client = LiveClient([role], explicit_live=True)
    requests = []

    def transport(request, timeout):
        requests.append(request)
        if request.full_url.endswith("/models"):
            return io.StringIO(
                json.dumps(
                    {
                        "data": [
                            {
                                "id": role.model,
                                "context_length": 40000,
                                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                            }
                        ]
                    }
                )
            )
        return io.StringIO(
            json.dumps(
                {
                    "id": "response-1",
                    "model": role.model,
                    "provider": "upstream",
                    "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 4, "cost": 0.001},
                }
            )
        )

    monkeypatch.setattr("urllib.request.urlopen", transport)
    validation = run(client.validate_models([role]))
    assert len(requests) == 1 and requests[0].get_method() == "GET"
    assert validation[0]["context_length"] == 40000
    result = run(CallManager(client, tmp_path, budget_usd=2).call(role, messages(), "final"))
    assert result.record["returned_provider"] == "upstream"
    assert result.record["input_tokens"] == 12
    assert result.record["cost_usd"] == 0.001
    assert "unit-test-secret" not in (tmp_path / "calls.jsonl").read_text()


def test_live_exact_model_validation_and_substitution(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only")
    role = hosted_role()
    client = LiveClient([role], explicit_live=True)
    with pytest.raises(ModelValidationError, match="Validate"):
        run(client.complete(role, messages(), "final"))
    monkeypatch.setattr(client, "_request", lambda *args: {"data": [{"id": "other"}]})
    with pytest.raises(ModelValidationError, match="unavailable"):
        run(client.validate_models([role]))
    monkeypatch.setattr(client, "_request", lambda *args: {"data": [{"id": role.model}]})
    run(client.validate_models([role]))
    monkeypatch.setattr(client, "_request", lambda *args: {"model": "substituted"})
    with pytest.raises(ModelValidationError, match="substitution"):
        run(client.complete(role, messages(), "final"))


def test_context_and_concurrency_limits(tmp_path):
    client = FakeClient()
    with pytest.raises(ValueError, match="concurrency"):
        CallManager(client, tmp_path, concurrency=4)
    with pytest.raises(ProviderError, match="context limit"):
        run(CallManager(client, tmp_path).call(RoleConfig(context_limit=10), messages(), "final"))
    assert not client.calls


def test_live_catalog_pricing_cannot_understate_reservations(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only")
    role = hosted_role()
    client = LiveClient([role], explicit_live=True)
    entry = {"id": role.model, "pricing": {"prompt": "0.000002", "completion": "0.000004"}}
    monkeypatch.setattr(client, "_request", lambda *args: {"data": [entry]})
    with pytest.raises(ModelValidationError, match="below the catalog price"):
        run(client.validate_models([role]))
    role = role.model_copy(update={"input_per_million": 2.0, "output_per_million": 4.0})
    result = run(client.validate_models([role]))[0]
    assert result["catalog_input_per_million"] == 2.0
    assert result["catalog_output_per_million"] == 4.0
    assert not result["pricing_manually_verified_required"]
