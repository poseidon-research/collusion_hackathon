"""Evidence-aware planning must cover real protocol messages without requests."""

import asyncio
from pathlib import Path

import pytest

from src.collusion_detection.config import CORE_CONDITIONS, StudyConfig
from src.collusion_detection.engine import Engine
from src.collusion_detection.evidence import build_view
from src.collusion_detection.importer import import_episode
from src.collusion_detection.planning import _condition_calls, check_live_plan, plan
from src.collusion_detection.providers import CallManager, FakeClient, estimate_input_tokens
from src.collusion_detection.schemas import Episode, Event, Participant
from src.collusion_detection.storage import canonical

ROOT = Path(__file__).resolve().parents[2]
REAL_LOG = ROOT / "data/20250517_100835_claude_buyers-claude_sellers-seller_comms-5/auction_results.md"


def config(*, hosted=False, context=500_000, retries=0):
    value = StudyConfig(conditions=list(CORE_CONDITIONS), retries=retries, budget_usd=25)
    for role in value.roles.values():
        role.context_limit = context
        role.max_output_tokens = 3200
        if hosted:
            role.provider = "openrouter"
            role.model = "openai/gpt-4.1-nano"
            role.input_per_million = 0.1
            role.output_per_million = 0.4
    return value.check()


def unicode_episode():
    # Astral characters exercise four-byte UTF-8 and NULs exercise JSON escaping.
    events = [
        Event(
            event_id=f"e{i}",
            round=i,
            event_type="seller_message",
            actor="S01",
            text="😀" * 5500 + "\x00" * 100,
            source_ref=f"round/{i}",
            available_round=i + 1,
            observation="message",
        )
        for i in (1, 2, 3)
    ]
    return Episode(
        episode_id="unicode",
        group_id="unicode",
        rounds=3,
        events=events,
        participants=[Participant(alias="S01", role="seller"), Participant(alias="S02", role="seller")],
        oversight="disabled",
        synthetic=True,
    )


def final():
    return {
        "implemented_collusion_probability": 0.5,
        "agreement": "insufficient_evidence",
        "implementation": "insufficient_evidence",
        "suspected_coalition": [],
        "supporting_evidence_ids": [],
        "counterevidence": [],
        "explanation": "Mocked",
        "limitations": [],
    }


def report():
    return {
        "candidate_arrangement": "x",
        "sellers": ["S01", "S02"],
        "support": [{"event_id": f"e{i}", "quote": "😀" * 5500} for i in (1, 2, 3)],
        "relationships": [],
        "counterevidence": [],
        "independent_explanations": [],
        "limitations": [],
    }


def answer():
    return {
        "answer": "😀" * 3000,
        "excerpts": [{"event_id": f"e{i}", "quote": "😀" * 4000} for i in (1, 2)],
        "limitations": [],
    }


@pytest.mark.parametrize(
    "condition", ["M_RAW", "M_EAE", "M_LINKED", "M_INTERACTIVE", "MAO_STATIC_REVIEW", "MAO_JUDGE_REVIEW"]
)
def test_actual_unicode_protocol_calls_fit_constructed_bounds(tmp_path, condition):
    episode = unicode_episode()
    if condition.endswith("INTERACTIVE"):
        scripted = [
            report(),
            {"action": "ASK", "question": "\x00" * 600},
            answer(),
            {"action": "ASK", "question": "\x00" * 600},
            answer(),
            final(),
        ]
    elif condition.endswith("EAE"):
        # Exact spans approach the aggregate serialized selection cap.
        scripted = [{"selections": [{"event_id": f"e{i}", "start": 0, "end": 5500} for i in (1, 2, 3)]}, final()]
    elif condition.endswith("STATIC_REVIEW"):
        scripted = [report(), report(), final()]
    elif condition.endswith("JUDGE_REVIEW"):
        scripted = [final(), final()]
    elif condition.endswith("LINKED"):
        scripted = [report(), final()]
    else:
        scripted = [final()]
    fake = FakeClient(scripted)
    study = config()
    manager = CallManager(fake, tmp_path / "cache")
    result = asyncio.run(Engine(study, manager).run(episode, condition))
    assert result["status"] == "ok", result.get("error")
    bounded = _condition_calls(study, episode, condition)
    assert len(fake.calls) == len(bounded)
    for observed, bound in zip(fake.calls, bounded):
        assert observed["kind"] == bound["kind"]
        assert estimate_input_tokens(observed["messages"]) <= bound["input_tokens_bound"]


def test_exact_raw_bound_includes_native_schema_overhead():
    episode, _ = import_episode(REAL_LOG)
    study = config()
    engine = Engine(study, None)
    from src.collusion_detection.schemas import Assessment

    messages = [
        engine.system("final", Assessment),
        {"role": "user", "content": canonical({"evidence": build_view(episode, "MAO").model_dump()})},
    ]
    bound = _condition_calls(study, episode, "MAO_RAW")[0]["input_tokens_bound"]
    assert bound == estimate_input_tokens(messages)
    framing_only = sum(len(m["content"].encode("utf-8")) + 32 for m in messages) + 32
    assert bound > framing_only  # Native strict JSON schema is sent separately too.


def test_hosted_context_warnings_block_live_but_fake_planning_remains_available():
    episode, _ = import_episode(REAL_LOG)
    hosted = config(hosted=True, context=32_000)
    expansion = plan(hosted, [episode])
    assert expansion["context_warnings"]
    with pytest.raises(ValueError, match="context"):
        check_live_plan(expansion, hosted)
    offline = plan(config(context=32_000), [episode])
    assert offline["context_warnings"]
    assert offline["estimated_usd_upper_bound"] == 0
    assert offline["completion_requests_sent"] == 0


def test_retries_and_replicates_scale_all_possible_calls_and_costs():
    episode, _ = import_episode(REAL_LOG)
    single = config(hosted=True)
    base = plan(single, [episode])
    repeated = config(hosted=True, retries=2)
    repeated.replicates = 3
    scaled = plan(repeated, [episode])
    assert scaled["estimated_usd_upper_bound"] == pytest.approx(base["estimated_usd_upper_bound"] * 9)
    for before, after in zip(base["conditions"], scaled["conditions"]):
        assert after["logical_calls_max"] == before["logical_calls_max"] * 3
        assert after["completion_attempts_max"] == before["completion_attempts_max"] * 9
    assert scaled["completion_requests_sent"] == 0


def test_real_bundled_40_episode_size_pilot_is_feasible_without_context_warnings():
    # Repeating a representative full real log isolates planning/size accounting;
    # no scientific evaluation or claim of 40 independent episodes is made.
    episode, _ = import_episode(REAL_LOG)
    episodes = [episode.model_copy(update={"episode_id": f"planning-only-{i}"}) for i in range(40)]
    study = config(hosted=True)
    expansion = plan(study, episodes)
    assert expansion["episodes"] == 40
    assert len(expansion["conditions"]) == 9
    assert not expansion["context_warnings"]
    assert 0 < expansion["estimated_usd_upper_bound"] < 25
    assert expansion["completion_requests_sent"] == 0
    check_live_plan(expansion, study)
    linked = next(r for r in expansion["conditions"] if r["condition"] == "MAO_LINKED")
    interactive = next(r for r in expansion["conditions"] if r["condition"] == "MAO_INTERACTIVE")
    assert linked["assistant_calls_per_episode_max"] == 1
    assert interactive["assistant_calls_per_episode_max"] == 3


@pytest.mark.parametrize("condition", CORE_CONDITIONS + ["MAO_STATIC_REVIEW", "MAO_JUDGE_REVIEW"])
def test_v3_native_reference_enums_and_hydrated_quotes_fit_plan(tmp_path, condition):
    episode = unicode_episode()
    study = config()
    study.prompt_version = "v3"
    reference_report = report()
    reference_report["support"] = [{"event_id": f"e{i}"} for i in (1, 2, 3)]
    reference_answer = answer()
    reference_answer["excerpts"] = [{"event_id": "e1"}]
    if condition.endswith("INTERACTIVE"):
        scripted = [
            reference_report,
            {"action": "ASK", "question": "\x00" * 600},
            reference_answer,
            {"action": "ASK", "question": "\x00" * 600},
            reference_answer,
            final(),
        ]
    elif condition.endswith("EAE"):
        scripted = [{"selections": [{"event_id": "e1"}, {"event_id": "e2"}]}, final()]
    elif condition.endswith("STATIC_REVIEW"):
        scripted = [reference_report, reference_report, final()]
    elif condition.endswith("JUDGE_REVIEW"):
        scripted = [final(), final()]
    elif condition.endswith("LINKED"):
        scripted = [reference_report, final()]
    else:
        scripted = [final()]
    fake = FakeClient(scripted)
    result = asyncio.run(Engine(study, CallManager(fake, tmp_path / "cache")).run(episode, condition))
    assert result["status"] == "ok", result.get("error")
    bounds = _condition_calls(study, episode, condition)
    assert len(fake.calls) == len(bounds)
    for observed, bound in zip(fake.calls, bounds):
        assert observed["kind"] == bound["kind"]
        assert estimate_input_tokens(observed["messages"]) <= bound["input_tokens_bound"]
