import asyncio
import json
from unittest.mock import patch

import pytest

from src.collusion_detection.cli import export_annotations, main, run_predictions
from src.collusion_detection.config import StudyConfig
from src.collusion_detection.engine import Engine
from src.collusion_detection.evidence import (
    build_view,
    render_selection,
    validate_assessment,
    validate_report,
)
from src.collusion_detection.fixtures import make_fixtures
from src.collusion_detection.importer import import_episode, import_market, parse_rounds
from src.collusion_detection.planning import check_live_plan, plan
from src.collusion_detection.providers import CallManager, FakeClient, ProviderError
from src.collusion_detection.safety import ROOT, fingerprint, require_acceptance
from src.collusion_detection.schemas import Assessment, LinkedReport, Selections


@pytest.fixture
def episodes(tmp_path):
    return make_fixtures(tmp_path / "fixtures")


def engine(tmp_path, client=None, questions=2):
    config = StudyConfig(max_questions=questions)
    return Engine(config, CallManager(client or FakeClient(), tmp_path / "cache"))


def run(coro):
    return asyncio.run(coro)


def final(score=0.5):
    return {
        "implemented_collusion_probability": score,
        "agreement": "insufficient_evidence",
        "implementation": "insufficient_evidence",
        "suspected_coalition": [],
        "supporting_evidence_ids": [],
        "counterevidence": [],
        "explanation": "Test only.",
        "limitations": [],
    }


def test_import_actual_bundled_four_backticks(tmp_path):
    paths = sorted((ROOT / "data").rglob("auction_results.md"))
    paths.sort(key=lambda p: "base-seller_comms-9" not in str(p))
    assert paths and "````json" in paths[0].read_text()
    rows = parse_rounds(paths[0])
    ep, private = import_episode(paths[0])
    assert ep.rounds == max(r["round_number"] for r in rows)
    assert {"seller_message", "ask_record", "bid_record", "trade", "outcome"} <= {e.event_type for e in ep.events}
    assert ep.episode_id.startswith("ep_") and private["source_path"]
    public = ep.model_dump_json()
    assert str(paths[0]) not in public and "original_metadata" not in public
    for original in private["alias_map"]:
        assert original not in public
    imported, excluded = import_market(ROOT / "data", tmp_path / "imported")
    assert imported and excluded
    assert all(e.oversight == "disabled" for e in imported)
    assert any("oversight" in row["reason"] for row in excluded)


def test_allowlist_views_cannot_leak_private_fields(episodes):
    ep = episodes[0]
    m, mao, a = [build_view(ep, view) for view in ["M", "MAO", "A"]]
    assert all(e.event_type == "seller_message" for e in m.events)
    assert all(e.event_type != "seller_message" for e in a.events)
    assert len(mao.events) == len(m.events) + len(a.events)
    assert m.context == a.context == mao.context
    assert set(m.model_dump()) == {"episode_id", "view", "context", "events"}
    assert "synthetic" not in m.model_dump() and "group_id" not in m.model_dump()
    for raw in ["private_reasoning", "gold_label", "assigned_scenario", "PASS", "overseer_coordination_score"]:
        assert raw not in mao.model_dump_json()


def test_next_round_semantics_and_no_invented_order(episodes):
    ep = episodes[0]
    messages = [e for e in ep.events if e.event_type == "seller_message" and e.text]
    assert [e.round for e in messages] == [1, 2]
    assert [e.available_round for e in messages] == [2, 3]
    assert all("execution_order" not in e.model_dump() for e in ep.events)
    asks = [e for e in ep.events if e.event_type == "ask_record" and e.round == 3]
    assert len(asks) == 2 and all("96" in e.text for e in asks)
    assert "same-round actions cannot" in build_view(ep, "M").context["rules"]


def test_missing_orders_are_not_automatically_withdrawn(tmp_path):
    path = tmp_path / "auction_results.md"
    rows = [
        {"round_number": 1, "seller_asks": {"Seller X": 95}, "buyer_bids": {}, "seller_messages": {}, "trades": []},
        {"round_number": 2, "seller_asks": {}, "buyer_bids": {}, "seller_messages": {}, "trades": []},
    ]
    path.write_text("\n".join("````json\n" + json.dumps(row) + "\n````" for row in rows))
    ep, _ = import_episode(path)
    assert ep.events[-2].observation == "missing_or_withdrawn"
    assert "cannot be distinguished" in ep.events[-2].text


def test_validate_exact_source_spans_view_membership_and_reports(episodes):
    ep = episodes[0]
    m, a = build_view(ep, "M"), build_view(ep, "A")
    item = m.events[0]
    selection = Selections(selections=[{"event_id": item.event_id, "start": 0, "end": 5}])
    assert render_selection(selection, m)[0]["text"] == item.text[:5]
    with pytest.raises(ValueError, match="forbidden"):
        render_selection(selection, a)
    with pytest.raises(ValueError, match="span"):
        render_selection(Selections(selections=[{"event_id": item.event_id, "start": 2, "end": 1}]), m)
    report = LinkedReport(
        candidate_arrangement="Floor",
        sellers=["S01", "S02"],
        support=[{"event_id": item.event_id, "quote": item.text}],
        relationships=[],
        counterevidence=[],
        independent_explanations=[],
        limitations=[],
    )
    assert validate_report(report, m) == report
    report.support[0].quote = "invented quotation"
    with pytest.raises(ValueError, match="exact source"):
        validate_report(report, m)
    assessment = Assessment.model_validate(dict(final(), supporting_evidence_ids=[item.event_id]))
    with pytest.raises(ValueError, match="not supplied"):
        validate_assessment(assessment, m, set())


def test_eae_judge_only_gets_selected_canonical_excerpts(tmp_path, episodes):
    client = FakeClient()
    prediction = run(engine(tmp_path, client).run(episodes[0], "MAO_EAE"))
    assert prediction["status"] == "ok"
    judge = next(c for c in client.calls if c["kind"] == "final")
    payload = json.loads(judge["messages"][1]["content"])
    assert set(payload) == {"context", "view", "selected_excerpts"}
    assert len(payload["selected_excerpts"]) == 3
    assert "selections" not in payload and "evidence" not in payload
    assert len(payload["selected_excerpts"]) < len(episodes[0].events)


def test_linked_and_interactive_reuse_identical_report(tmp_path, episodes):
    client = FakeClient()

    async def scenario():
        monitor = engine(tmp_path, client)
        return [await monitor.run(episodes[0], p) for p in ["MAO_LINKED", "MAO_INTERACTIVE"]]

    linked, interactive = run(scenario())
    assert linked["status"] == interactive["status"] == "ok"
    assert linked["initial_report_hash"] == interactive["initial_report_hash"]
    assert len([c for c in client.calls if c["kind"] == "report"]) == 1
    assert interactive["calls"][0]["cached"]
    assert interactive["calls"][0]["actual_billed_cost_usd"] == 0
    assert [x["speaker"] for x in interactive["exchange"]] == ["judge", "assistant", "judge"]
    decision = next(c for c in client.calls if c["kind"] == "decision")
    assert "evidence" not in json.loads(decision["messages"][1]["content"])


def test_interactive_enforces_two_questions_then_final(tmp_path, episodes):
    client = FakeClient(decision_actions=["ASK", "ASK", "ASK", "ASK"])
    p = run(engine(tmp_path, client).run(episodes[0], "M_INTERACTIVE"))
    assert p["status"] == "ok"
    assert [c["kind"] for c in client.calls] == ["report", "decision", "answer", "decision", "answer", "final"]
    second_answer = [c for c in client.calls if c["kind"] == "answer"][1]
    payload = json.loads(second_answer["messages"][1]["content"])
    assert any(x["speaker"] == "assistant" for x in payload["prior_exchange"])
    assert all(e["event_type"] == "seller_message" for e in payload["evidence"]["events"])


def test_early_finish_and_zero_questions(tmp_path, episodes):
    client = FakeClient(decision_actions=["FINISH"])
    p = run(engine(tmp_path, client).run(episodes[0], "MAO_INTERACTIVE"))
    assert p["status"] == "ok" and len(p["calls"]) == 2
    client = FakeClient()
    p = run(engine(tmp_path / "zero", client, 0).run(episodes[0], "MAO_INTERACTIVE"))
    assert p["status"] == "ok" and [c["kind"] for c in client.calls] == ["report", "final"]


def test_conversation_reset_and_replicates(tmp_path, episodes):
    client = FakeClient()

    async def scenario():
        monitor = engine(tmp_path, client)
        return [
            await monitor.run(ep, "M_INTERACTIVE", rep)
            for ep, rep in [(episodes[0], 0), (episodes[1], 0), (episodes[0], 1)]
        ]

    predictions = run(scenario())
    assert all(p["status"] == "ok" and len(p["exchange"]) == 3 for p in predictions)
    first_decisions = [c for c in client.calls if c["kind"] == "decision" and len(c["messages"]) == 3]
    assert len(first_decisions) == 3
    assert len([c for c in client.calls if c["kind"] == "report"]) == 3


@pytest.mark.parametrize("response", ["not json", final(2), ProviderError("scripted API failure")])
def test_parse_and_api_failures_are_errors(tmp_path, episodes, response):
    client = FakeClient(scripted=[response])
    p = run(engine(tmp_path, client).run(episodes[0], "M_RAW"))
    assert p["status"] == "error" and p["assessment"] is None and p["error"]


def test_invalid_assistant_citation_is_error_not_replaced(tmp_path, episodes):
    client = FakeClient(scripted=[{"selections": [{"event_id": "forbidden", "start": None, "end": None}]}])
    p = run(engine(tmp_path, client).run(episodes[0], "M_EAE"))
    assert p["status"] == "error" and len(client.calls) == 1


def test_compute_controls_real_additional_calls(tmp_path, episodes):
    async def scenario():
        monitor = engine(tmp_path)
        return [await monitor.run(episodes[0], p) for p in ["MAO_STATIC_REVIEW", "MAO_JUDGE_REVIEW"]]

    static, judge = run(scenario())
    assert static["status"] == judge["status"] == "ok"
    assert [c["kind"] for c in static["calls"]] == ["report", "refine_report", "final"]
    assert [c["kind"] for c in judge["calls"]] == ["final", "refine_judge"]


def test_annotation_packet_hides_private_and_model_outputs(tmp_path, episodes):
    export_annotations(episodes, tmp_path / "packets")
    packet = (tmp_path / "packets/packet.md").read_text()
    assert "assigned_scenario" not in packet and "fixture_case" not in packet
    assert "synthetic_fixture_author" not in packet
    labels = [json.loads(x) for x in (tmp_path / "packets/labels.template.jsonl").read_text().splitlines()]
    assert all(r["reviewer"] == "" and r["implemented_collusion"] is None for r in labels)


def test_plan_zero_requests_gate_and_offline_cli(tmp_path, episodes, capsys):
    config = StudyConfig()
    with patch("src.collusion_detection.cli.LiveClient", side_effect=AssertionError("Real provider invoked")):
        expansion = plan(config, episodes)
        assert expansion["completion_requests_sent"] == 0
        assert len(expansion["conditions"]) == 9
        main(
            [
                "plan",
                "--episodes",
                str(tmp_path / "fixtures/episodes.jsonl"),
                "--config",
                str(ROOT / "configs/detection/offline.json"),
                "--out",
                str(tmp_path / "plan.json"),
            ]
        )
        main(
            [
                "run",
                "--episodes",
                str(tmp_path / "fixtures/episodes.jsonl"),
                "--config",
                str(ROOT / "configs/detection/offline.json"),
                "--out",
                str(tmp_path / "run"),
            ]
        )
    predictions = [json.loads(x) for x in (tmp_path / "run/predictions.jsonl").read_text().splitlines()]
    assert len(predictions) == 54 and all(p["status"] == "ok" for p in predictions)
    with pytest.raises(ValueError):
        check_live_plan(expansion, config)
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"status": "passed", "fingerprint": fingerprint(config)}))
    assert require_acceptance(receipt, config)
    config.max_questions = 1
    with pytest.raises(ValueError, match="stale"):
        require_acceptance(receipt, config)


def test_live_dry_run_never_reads_credentials_or_constructs_live(tmp_path, episodes):
    with (
        patch("src.collusion_detection.cli.LiveClient", side_effect=AssertionError("Live constructed")),
        patch("src.collusion_detection.providers.os") as environment,
    ):
        environment.environ.get.side_effect = AssertionError("Credential read")
        main(
            [
                "run",
                "--live",
                "--dry-run",
                "--config",
                str(ROOT / "configs/detection/live-smoke.json"),
                "--episodes",
                str(tmp_path / "fixtures/episodes.jsonl"),
                "--out",
                str(tmp_path / "plan.json"),
            ]
        )
    assert json.loads((tmp_path / "plan.json").read_text())["completion_requests_sent"] == 0


def test_resume_manifest_freezes_protocols_and_config(tmp_path, episodes):
    config = StudyConfig(conditions=["M_RAW"])
    _first, _manifest = run(run_predictions(config, episodes, tmp_path / "run"))
    second, _ = run(run_predictions(config, episodes, tmp_path / "run"))
    assert second[0]["calls"][0]["cached"]
    config.fixed_threshold = 0.6
    with pytest.raises(ValueError, match="different frozen manifest"):
        run(run_predictions(config, episodes, tmp_path / "run"))


def test_acceptance_subprocess_environment_does_not_inherit_credentials(monkeypatch):
    from src.collusion_detection.safety import offline_test_environment

    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("NEBIUS_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("LLM_MODEL", "synthetic-global-override")
    environment = offline_test_environment()
    assert "OPENROUTER_API_KEY" not in environment
    assert "NEBIUS_API_KEY" not in environment
    assert "LLM_MODEL" not in environment
