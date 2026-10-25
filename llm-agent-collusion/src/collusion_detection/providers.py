"""Isolated hosted inference, deterministic offline doubles, and auditable calls.

Nothing in this module reads credentials or opens connections at import time.
Only constructing ``LiveClient(..., explicit_live=True)`` reads credentials.
The caller must perform model-catalog validation before hosted completions.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .structured import provider_format


class RoleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    provider: Literal["fake", "openrouter", "nebius"] = "fake"
    model: str = "offline-deterministic"
    base_url: str | None = None
    api_key_env: str | None = None
    context_limit: int = Field(default=32000, gt=0)
    input_per_million: float | None = Field(default=None, ge=0)
    output_per_million: float | None = Field(default=None, ge=0)
    max_output_tokens: int = Field(default=1600, gt=0)
    temperature: float = Field(default=0, ge=0)


@dataclass
class ProviderResponse:
    text: str
    requested_model: str | None = None
    returned_model: str | None = None
    provider: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    billed_cost_usd: float | None = None
    response_id: str | None = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CallResult:
    text: str
    record: dict[str, Any]


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, retry_after: float | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


class BudgetExceeded(ProviderError):
    pass


class ModelValidationError(ProviderError):
    pass


class ModelClient(Protocol):
    async def complete(self, role: RoleConfig, messages: list[dict[str, str]], kind: str) -> ProviderResponse: ...


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def estimate_input_tokens(messages: list[dict[str, str]]) -> int:
    """Byte-based upper planning bound, including generous message framing."""
    tokens = sum(len(message.get("content", "").encode("utf-8")) + 32 for message in messages) + 32
    output_format = provider_format(messages)
    if output_format is not None:
        tokens += len(_canonical(output_format).encode("utf-8")) + 128
    return tokens


def estimate_reservation(role: RoleConfig, messages: list[dict[str, str]]) -> float | None:
    if role.provider == "fake":
        return 0.0
    if role.input_per_million is None or role.output_per_million is None:
        return None
    return (
        estimate_input_tokens(messages) * role.input_per_million + role.max_output_tokens * role.output_per_million
    ) / 1_000_000


def _extract_events(messages: list[dict[str, str]]) -> list[dict[str, Any]]:
    events: dict[str, dict[str, Any]] = {}

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            identifier = item.get("event_id")
            if identifier and ("text" in item or "content" in item or "event_type" in item or "type" in item):
                events[str(identifier)] = item
            for value in item.values():
                visit(value)
        elif isinstance(item, list):
            for value in item:
                visit(value)

    decoder = json.JSONDecoder()
    for message in messages:
        content = message.get("content", "")
        try:
            visit(json.loads(content))
        except (ValueError, TypeError):
            # Prompts can surround a single canonical JSON evidence block.
            for index, char in enumerate(content):
                if char in "{[":
                    try:
                        value, _ = decoder.raw_decode(content[index:])
                        visit(value)
                    except ValueError:
                        continue
    return list(events.values())


def _assessment() -> dict[str, Any]:
    return {
        "implemented_collusion_probability": 0.5,
        "agreement": "insufficient_evidence",
        "implementation": "insufficient_evidence",
        "suspected_coalition": [],
        "supporting_evidence_ids": [],
        "counterevidence": [],
        "explanation": "Deterministic offline fixture; no model capability or scientific result is measured.",
        "limitations": ["Mocked inference; this score has no substantive evidential interpretation."],
    }


class FakeClient:
    """Deterministic doubles with an optional sequence of responses or errors.

    ``scripted`` entries may be exceptions, ProviderResponse objects, raw text,
    or JSON-compatible dictionaries. Unscripted decisions ask once per isolated
    conversation, then finish. Call history is in-memory and never uses keys.
    """

    def __init__(self, scripted: list[Any] | None = None, *, decision_actions: list[str] | None = None):
        self.scripted = list(scripted or [])
        self.decision_actions = list(decision_actions) if decision_actions is not None else None
        self.calls: list[dict[str, Any]] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def complete(self, role: RoleConfig, messages: list[dict[str, str]], kind: str) -> ProviderResponse:
        self.calls.append({"role": role.model_dump(), "messages": json.loads(_canonical(messages)), "kind": kind})
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0)
            if self.scripted:
                value = self.scripted.pop(0)
                if isinstance(value, Exception):
                    raise value
                if isinstance(value, ProviderResponse):
                    return value
            else:
                events = _extract_events(messages)
                # Only the trusted system schema selects the v3 pointer shape.
                from .structured import response_contract

                pointer_mode = "EventReference" in (response_contract(messages) or {}).get("$defs", {})
                if pointer_mode:
                    events = [event for event in events if str(event.get("text", event.get("content", "")))]
                selected = events[:3]
                support = [
                    {"event_id": str(event["event_id"]), "quote": str(event.get("text", event.get("content", "")))}
                    for event in selected
                ]
                if pointer_mode:
                    support = [{"event_id": item["event_id"]} for item in support]
                if kind == "market_seller":
                    value = {
                        "ask": 95.0,
                        "new_memory": "Mocked market decision: ask $95.00.",
                        "message_to_sellers": "I am independently considering the current order book.",
                        "scratch_pad_update": "Offline fixture only.",
                    }
                elif kind == "market_buyer":
                    value = {
                        "bid": 97.0,
                        "new_memory": "Mocked market decision: bid $97.00.",
                        "scratch_pad_update": "Offline fixture only.",
                    }
                elif kind in {"selection", "select", "eae"}:
                    value = {
                        "selections": support
                        if pointer_mode
                        else [{"event_id": item["event_id"], "start": None, "end": None} for item in support]
                    }
                elif kind in {"report", "refine_report", "linked"}:
                    value = {
                        "candidate_arrangement": "Undetermined in deterministic offline fixture.",
                        "sellers": [],
                        "support": support,
                        "relationships": [],
                        "counterevidence": [],
                        "independent_explanations": ["Independent competitive behavior remains possible."],
                        "limitations": ["Mocked report; interpretation is not evaluated."],
                    }
                elif kind == "answer":
                    value = {
                        "answer": "The permitted source excerpts are attached; inference is mocked.",
                        "excerpts": support,
                        "limitations": ["Only assigned source evidence is available."],
                    }
                elif kind == "decision":
                    # An assistant reply after an ASK is supplied as a distinct role message.
                    has_answer = any('"answer"' in m.get("content", "") for m in messages[1:])
                    has_ask = any(m.get("role") == "assistant" and '"ASK"' in m.get("content", "") for m in messages)
                    action = (
                        self.decision_actions.pop(0)
                        if self.decision_actions
                        else ("FINISH" if has_answer or has_ask else "ASK")
                    )
                    value = (
                        {"action": "ASK", "question": "What observable counterevidence limits the report?"}
                        if action == "ASK"
                        else {"action": "FINISH", "assessment": _assessment()}
                    )
                else:
                    value = _assessment()
            text = value if isinstance(value, str) else _canonical(value)
            return ProviderResponse(
                text=text,
                requested_model=role.model,
                returned_model=role.model,
                provider="fake",
                input_tokens=max(1, math.ceil(len(_canonical(messages)) / 4)),
                output_tokens=max(1, math.ceil(len(text) / 4)),
                billed_cost_usd=0.0,
                raw_metadata={"mocked": True},
            )
        finally:
            self.in_flight -= 1


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        except (ValueError, TypeError):
            return None


class LiveClient:
    """OpenAI-compatible adapters; construction alone does not send requests."""

    DEFAULT_BASE_URLS: ClassVar[dict[str, str]] = {
        "openrouter": "https://openrouter.ai/api/v1",
        "nebius": "https://api.tokenfactory.nebius.com/v1",
    }
    DEFAULT_KEY_ENVS: ClassVar[dict[str, str]] = {"openrouter": "OPENROUTER_API_KEY", "nebius": "NEBIUS_API_KEY"}

    def __init__(self, roles: list[RoleConfig], *, explicit_live: bool = False, timeout: float = 60):
        if not explicit_live:
            raise ProviderError("Hosted inference requires explicit --live authorization.")
        if os.environ.get("LLM_MODEL"):
            raise ProviderError("Unset LLM_MODEL before live inference; per-role model IDs must be explicit.")
        self.timeout = timeout
        self._keys: dict[str, str] = {}
        self._validated: set[str] = set()
        for role in roles:
            if role.provider == "fake":
                continue
            name = role.api_key_env or self.DEFAULT_KEY_ENVS[role.provider]
            key = os.environ.get(name)
            if not key:
                raise ProviderError(f"Missing credential environment variable {name}.")
            self._keys[_digest(role.model_dump())] = key

    def _request(self, role: RoleConfig, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        key = self._keys.get(_digest(role.model_dump()))
        if key is None:
            raise ProviderError("Role was not explicitly configured when the live client was constructed.")
        base = (role.base_url or self.DEFAULT_BASE_URLS[role.provider]).rstrip("/")
        if not base.startswith("https://"):
            raise ProviderError("Live provider base URLs must use HTTPS.")
        request = urllib.request.Request(
            base + path,
            data=_canonical(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            # Response error bodies can contain echoed inputs or credentials; retain status only.
            raise ProviderError(
                f"Provider HTTP {error.code}",
                retryable=error.code in {408, 429, 500, 502, 503, 504},
                retry_after=_retry_after(error.headers.get("Retry-After")),
            ) from None
        except (urllib.error.URLError, TimeoutError):
            raise ProviderError("Provider transport failure", retryable=True) from None
        except (ValueError, TypeError):
            raise ProviderError("Provider returned malformed JSON") from None

    async def validate_models(self, roles: list[RoleConfig]) -> list[dict[str, Any]]:
        """Authenticated model catalog GETs only; never sends a completion."""
        results = []
        for role in roles:
            if role.provider == "fake":
                continue
            payload = await asyncio.to_thread(self._request, role, "/models")
            entry = next((item for item in payload.get("data", []) if item.get("id") == role.model), None)
            if entry is None:
                raise ModelValidationError(f"Exact configured model is unavailable: {role.provider}/{role.model}")
            context = entry.get("context_length", entry.get("context_window"))
            if context is not None and role.context_limit > int(context):
                raise ModelValidationError("Configured context limit exceeds the provider model catalog.")
            # Some compatible catalogs omit limits/capabilities; record that limitation explicitly.
            architecture = entry.get("architecture", {})
            outputs = architecture.get("output_modalities")
            inputs = architecture.get("input_modalities")
            if outputs is not None and "text" not in outputs:
                raise ModelValidationError("Configured model does not advertise text output.")
            if inputs is not None and "text" not in inputs:
                raise ModelValidationError("Configured model does not advertise text input.")
            pricing = entry.get("pricing") or {}
            catalog_prices = {}
            for catalog_field, configured in (
                ("prompt", role.input_per_million),
                ("completion", role.output_per_million),
            ):
                raw_price = pricing.get(catalog_field)
                if raw_price is None:
                    catalog_prices[catalog_field] = None
                    continue
                try:
                    per_million = float(raw_price) * 1_000_000
                except (ValueError, TypeError):
                    raise ModelValidationError("Provider catalog contains an invalid token price.") from None
                if not math.isfinite(per_million) or per_million < 0:
                    raise ModelValidationError("Provider catalog contains an invalid token price.")
                catalog_prices[catalog_field] = per_million
                if configured is None or configured + 1e-12 < per_million:
                    raise ModelValidationError(
                        f"Configured {catalog_field} price is below the catalog price; "
                        "increase the configured per-million price before live inference."
                    )
            parameters = entry.get("supported_parameters")
            if parameters is not None and not {"structured_outputs", "response_format"} <= set(parameters):
                raise ModelValidationError(
                    "Configured model does not advertise required structured JSON output support."
                )
            self._validated.add(_digest(role.model_dump()))
            results.append(
                {
                    "provider": role.provider,
                    "model": role.model,
                    "context_length": context,
                    "context_limit_manually_configured": context is None,
                    "capabilities_manually_verified_required": inputs is None or outputs is None,
                    "pricing_manually_verified_required": any(value is None for value in catalog_prices.values()),
                    "catalog_input_per_million": catalog_prices["prompt"],
                    "catalog_output_per_million": catalog_prices["completion"],
                    "structured_output_manually_verified_required": parameters is None,
                }
            )
        return results

    async def complete(self, role: RoleConfig, messages: list[dict[str, str]], kind: str) -> ProviderResponse:
        if _digest(role.model_dump()) not in self._validated:
            raise ModelValidationError("Validate the exact provider/model catalog before hosted inference.")
        body = {
            "model": role.model,
            "messages": messages,
            "temperature": role.temperature,
            "max_tokens": role.max_output_tokens,
            "stream": False,
        }
        output_format = provider_format(messages)
        if output_format is not None:
            body["response_format"] = output_format
            if role.provider == "openrouter":
                body["provider"] = {"require_parameters": True}
        payload = await asyncio.to_thread(self._request, role, "/chat/completions", body)
        if payload.get("model") != role.model:
            raise ModelValidationError(
                "Provider returned a different or missing model ID; refusing silent substitution."
            )
        try:
            content = payload["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError
        except (KeyError, IndexError, TypeError):
            raise ProviderError("Provider response has no text completion") from None
        usage = payload.get("usage") or {}
        return ProviderResponse(
            text=content,
            requested_model=role.model,
            returned_model=payload["model"],
            provider=payload.get("provider", role.provider),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            billed_cost_usd=usage.get("cost"),
            response_id=payload.get("id"),
            raw_metadata={
                "usage": usage,
                "finish_reason": payload["choices"][0].get("finish_reason"),
                "response_format": output_format["type"] if output_format else None,
                "response_schema_hash": _digest(output_format) if output_format else None,
            },
        )


# Shared by every manager and event loop in this Python process. Distributed
# runners must use one orchestration process per shared credential/budget.
_GLOBAL_GATE = threading.BoundedSemaphore(3)


@asynccontextmanager
async def _global_gate():
    # A nonblocking acquire avoids blocking the event loop and cannot leak a
    # semaphore permit if cancellation happens while waiting for a thread.
    while not _GLOBAL_GATE.acquire(blocking=False):
        await asyncio.sleep(0.01)
    try:
        yield
    finally:
        _GLOBAL_GATE.release()


def _attempt_totals(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """A known final response cannot fill gaps left by earlier billed attempts."""
    totals: dict[str, Any] = {}
    for field_name in ("input_tokens", "output_tokens", "cost_usd"):
        values = [attempt.get(field_name) for attempt in attempts]
        known = [value for value in values if value is not None]
        totals[field_name] = sum(known) if values and len(known) == len(values) else None
        totals["known_" + field_name] = sum(known)
        totals["unknown_" + field_name + "_attempts"] = len(values) - len(known)
    totals["actual_billed_cost_usd"] = totals["cost_usd"]
    totals["accounted_cost_usd"] = sum(attempt.get("accounted_cost_usd") or 0.0 for attempt in attempts)
    return totals


class CallManager:
    def __init__(
        self,
        client: ModelClient,
        cache_dir: Path,
        budget_usd: float | None = None,
        concurrency: int = 3,
        retries: int = 2,
    ):
        if concurrency < 1 or concurrency > 3:
            raise ValueError("Shared-key concurrency must be between one and three.")
        if retries < 0 or retries > 5:
            raise ValueError("Retries must be bounded between zero and five.")
        if budget_usd is not None and (not math.isfinite(budget_usd) or budget_usd < 0):
            raise ValueError("Budget must be finite and nonnegative.")
        self.client = client
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.budget_usd = budget_usd
        self.retries = retries
        self._gate = asyncio.Semaphore(concurrency)
        self._budget_lock = asyncio.Lock()
        self._cache_locks: dict[str, asyncio.Lock] = {}
        self.records: list[dict[str, Any]] = []
        self.accounted_usd = 0.0
        ledger = self.cache_dir / "attempts.jsonl"
        if ledger.exists():
            for line in ledger.read_text().splitlines():
                value = json.loads(line)
                self.accounted_usd += value.get("accounted_cost_usd") or 0.0

    def _append(self, filename: str, value: dict[str, Any]) -> None:
        with (self.cache_dir / filename).open("a") as handle:
            handle.write(_canonical(value) + "\n")

    async def _reserve(self, reservation: float | None, live: bool) -> None:
        if live and (self.budget_usd is None or reservation is None):
            raise BudgetExceeded("Live calls require an explicit budget and configured input/output prices.")
        async with self._budget_lock:
            if reservation is not None:
                if self.budget_usd is not None and self.accounted_usd + reservation > self.budget_usd + 1e-12:
                    raise BudgetExceeded("Conservative call reservation would exceed the configured budget.")
                self.accounted_usd += reservation

    async def call(
        self, role: RoleConfig, messages: list[dict[str, str]], kind: str, replicate: int = 0, namespace: str = ""
    ) -> CallResult:
        if replicate < 0:
            raise ValueError("Replicate must be nonnegative.")
        request = {
            "role": role.model_dump(),
            "messages": messages,
            "kind": kind,
            "replicate": replicate,
            "namespace": namespace,
        }
        key = _digest(request)
        async with self._cache_locks.setdefault(key, asyncio.Lock()):
            cache = self.cache_dir / (key + ".json")
            if cache.exists():
                value = json.loads(cache.read_text())
                record = dict(value["record"], **_attempt_totals(value["record"].get("attempts", [])))
                record.update(cached=True, actual_billed_cost_usd=0.0, actual_latency_seconds=0.0)
                self.records.append(record)
                self._append("calls.jsonl", record)
                return CallResult(value["text"], record)
            tokens_bound = estimate_input_tokens(messages)
            if tokens_bound + role.max_output_tokens > role.context_limit:
                raise ProviderError("Conservative input plus output token bound exceeds the configured context limit.")
            reservation = estimate_reservation(role, messages)
            attempts = []
            started = time.monotonic()
            base_record = {
                "cache_key": key,
                "prompt_hash": _digest(messages),
                "config_hash": _digest(role.model_dump()),
                "requested_model": role.model,
                "requested_provider": role.provider,
                "kind": kind,
                "replicate": replicate,
                "namespace": namespace,
                "request": request,
                "cached": False,
                "mocked": role.provider == "fake",
            }
            response = None
            for attempt_index in range(self.retries + 1):
                try:
                    async with self._gate, _global_gate():
                        await self._reserve(reservation, role.provider != "fake")
                        attempt_started = time.monotonic()
                        try:
                            response = await self.client.complete(role, messages, kind)
                        except Exception as error:
                            elapsed = time.monotonic() - attempt_started
                            attempt = {
                                "cache_key": key,
                                "attempt": attempt_index + 1,
                                "error": str(error),
                                "error_type": type(error).__name__,
                                "latency_seconds": elapsed,
                                "input_tokens": None,
                                "output_tokens": None,
                                "cost_usd": None,
                                "reserved_cost_usd": reservation,
                                "accounted_cost_usd": reservation,
                                "usage_unknown": True,
                            }
                            attempts.append(attempt)
                            self._append("attempts.jsonl", attempt)
                            raise
                        elapsed = time.monotonic() - attempt_started
                        # Keep each reservation charged, even when billed usage is lower/unknown.
                        # If billed cost exceeds its bound, charge the overrun and stop further calls.
                        billed = response.billed_cost_usd
                        accounted = max(reservation or 0.0, billed or 0.0)
                        if reservation is not None and accounted > reservation:
                            async with self._budget_lock:
                                self.accounted_usd += accounted - reservation
                        attempt = {
                            "cache_key": key,
                            "attempt": attempt_index + 1,
                            "error": None,
                            "latency_seconds": elapsed,
                            "input_tokens": response.input_tokens,
                            "output_tokens": response.output_tokens,
                            "cost_usd": billed,
                            "reserved_cost_usd": reservation,
                            "accounted_cost_usd": accounted,
                            "usage_unknown": response.input_tokens is None or response.output_tokens is None,
                        }
                        attempts.append(attempt)
                        self._append("attempts.jsonl", attempt)
                    break
                except Exception as error:
                    retryable = isinstance(error, ProviderError) and error.retryable
                    if not retryable or attempt_index == self.retries:
                        record = dict(
                            base_record,
                            status="error",
                            error=str(error),
                            attempts=attempts,
                            latency_seconds=time.monotonic() - started,
                            **_attempt_totals(attempts),
                        )
                        self.records.append(record)
                        self._append("calls.jsonl", record)
                        raise
                    delay = error.retry_after if error.retry_after is not None else 0.25 * 2**attempt_index
                    if delay > 30:
                        # Never retry before Retry-After: fail instead of sleeping unboundedly.
                        record = dict(
                            base_record,
                            status="error",
                            error="Retry-After exceeds bounded retry window",
                            attempts=attempts,
                            latency_seconds=time.monotonic() - started,
                            **_attempt_totals(attempts),
                        )
                        self.records.append(record)
                        self._append("calls.jsonl", record)
                        raise ProviderError(record["error"]) from error
                    await asyncio.sleep(max(0, delay))
            assert response is not None
            record = dict(
                base_record,
                status="ok",
                response=response.text,
                returned_model=response.returned_model,
                returned_provider=response.provider,
                response_id=response.response_id,
                last_response_usage={
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "cost_usd": response.billed_cost_usd,
                },
                latency_seconds=time.monotonic() - started,
                attempts=attempts,
                response_metadata=response.raw_metadata,
                **_attempt_totals(attempts),
            )
            value = {"text": response.text, "record": record}
            temporary = cache.with_suffix(".tmp")
            temporary.write_text(_canonical(value))
            temporary.replace(cache)
            self.records.append(record)
            self._append("calls.jsonl", record)
            return CallResult(response.text, record)
