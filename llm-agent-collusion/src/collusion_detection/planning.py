"""Zero-request expansion using source sizes and validated response-size bounds."""

from .engine import Engine, assistant_schema, prompt_hashes
from .evidence import build_view
from .providers import estimate_input_tokens
from .schemas import Assessment, Decision
from .storage import canonical, digest

# These caps apply to serialized responses, before embedding dictionaries in JSON.
# Four UTF-8 bytes per character safely cover every Unicode scalar. Questions
# have a raw-character cap, so JSON control-character escaping needs six bytes.
REPORT_BYTES = 20_000 * 4
EXCERPT_BYTES = 18_000 * 4
ANSWER_BYTES = 12_000 * 4
QUESTION_BYTES = 600 * 6
ASK_BYTES = QUESTION_BYTES + 100
FIELD_FRAME_BYTES = 256


def _payload_bytes(payload, *variable_bytes):
    """Bound canonical payload bytes with empty placeholders for variable fields."""
    return len(canonical(payload).encode("utf-8")) + sum(variable_bytes) + FIELD_FRAME_BYTES * len(variable_bytes)


def _condition_calls(config, episode, condition):
    """Expand maximum calls with conservative input bounds and exact schemas.

    Synthetic ASCII content has the specified byte size; the real system prompt
    still lets estimate_input_tokens include the native structured-output schema.
    No model client is constructed or invoked.
    """
    engine = Engine(config, None)
    view, protocol = condition.split("_", 1)
    evidence = build_view(episode, view)
    source = canonical({"evidence": evidence.model_dump()})
    context = {"context": evidence.context, "view": view}
    pointer_mode = config.prompt_version == "v3"
    source_ids = [event.event_id for event in evidence.events] if pointer_mode else None
    selection_schema = assistant_schema("selection", config.prompt_version)
    report_schema = assistant_schema("report", config.prompt_version)
    answer_schema = assistant_schema("answer", config.prompt_version)
    calls = []

    def append(role, kind, schema, user_bytes=None, history=(), source_text=None, ceiling=False):
        if ceiling:
            bound = max(0, config.roles[role].context_limit - config.roles[role].max_output_tokens)
        else:
            content = source_text if source_text is not None else "x" * user_bytes
            # A judge's actual enum is restricted further to forwarded IDs.
            # The complete source-ID set conservatively bounds any subset's
            # serialized schema while preserving exact RAW/assistant bounds.
            system = engine.system(kind, schema, event_ids=source_ids) if pointer_mode else engine.system(kind, schema)
            messages = [system, {"role": "user", "content": content}]
            messages += list(history)
            bound = estimate_input_tokens(messages)
        calls.append(
            {
                "role": role,
                "kind": kind,
                "input_tokens_bound": bound,
                "bound_source": "context_ceiling" if ceiling else "evidence_and_validated_caps",
            }
        )

    if protocol in ("RAW", "JUDGE_REVIEW"):
        append("judge", "final", Assessment, source_text=source)
        if protocol == "JUDGE_REVIEW":
            # Assessment arrays have no serialized total cap; this optional
            # control therefore retains the hard context limit as its bound.
            append("judge", "refine_judge", Assessment, ceiling=True)
        return calls
    if protocol == "EAE":
        append("assistant", "selection", selection_schema, source_text=source)
        bound = _payload_bytes(dict(context, selected_excerpts=""), EXCERPT_BYTES)
        append("judge", "final", Assessment, bound)
        return calls

    append("assistant", "report", report_schema, source_text=source)
    if protocol == "STATIC_REVIEW":
        history = [
            {"role": "assistant", "content": "x" * REPORT_BYTES},
            {
                "role": "user",
                "content": "Review and refine your report for unsupported interpretations, "
                "counterevidence and timing. Return the same report schema. No judge questions.",
            },
        ]
        # Engine keeps the original report system prompt on this refinement call.
        append("assistant", "report", report_schema, history=history, source_text=source)
        calls[-1]["kind"] = "refine_report"
    judge_payload = _payload_bytes(dict(context, report=""), REPORT_BYTES)
    if protocol != "INTERACTIVE":
        append("judge", "final", Assessment, judge_payload)
        return calls

    history = []
    for question_index in range(config.max_questions):
        remaining = {"role": "user", "content": f"Remaining questions: {config.max_questions - question_index}."}
        append("judge", "decision", Decision, judge_payload, history=[*history, remaining])
        prior_bytes = question_index * (ASK_BYTES + ANSWER_BYTES + 2 * FIELD_FRAME_BYTES)
        answer_payload = _payload_bytes(
            {"evidence": evidence.model_dump(), "report": "", "prior_exchange": [], "question": ""},
            REPORT_BYTES,
            prior_bytes,
            QUESTION_BYTES,
        )
        append("assistant", "answer", answer_schema, answer_payload)
        history += [{"role": "assistant", "content": "x" * ASK_BYTES}, {"role": "user", "content": "x" * ANSWER_BYTES}]
    append(
        "judge",
        "final",
        Assessment,
        judge_payload,
        history=[*history, {"role": "user", "content": "Question limit reached. Give the final Assessment JSON now."}],
    )
    return calls


def plan(config, episodes):
    """Bound every possible paid attempt without padding every call to context size."""
    rows, total, unknown, warnings = [], 0.0, False, []
    for condition in config.conditions:
        protocol = condition.split("_", 1)[1]
        assistants, judges = {
            "RAW": (0, 1),
            "EAE": (1, 1),
            "LINKED": (1, 1),
            "INTERACTIVE": (1 + config.max_questions, 1 + config.max_questions),
            "STATIC_REVIEW": (2, 1),
            "JUDGE_REVIEW": (0, 2),
        }[protocol]
        maximum, condition_unknown, input_total, largest = 0.0, False, 0, 0
        for episode in episodes:
            for call_index, call in enumerate(_condition_calls(config, episode, condition)):
                role = config.roles[call["role"]]
                bound = call["input_tokens_bound"]
                largest = max(largest, bound)
                if bound + role.max_output_tokens > role.context_limit:
                    warnings.append(
                        {
                            "episode_id": episode.episode_id,
                            "condition": condition,
                            "role": call["role"],
                            "kind": call["kind"],
                            "call_index": call_index,
                            "provider": role.provider,
                            "input_tokens_upper_bound": bound,
                            "required_context_tokens": bound + role.max_output_tokens,
                            "configured_context_limit": role.context_limit,
                        }
                    )
                # Manager rejects oversized prompts before reserving or requesting.
                billable_input = min(bound, max(0, role.context_limit - role.max_output_tokens))
                multiplier = config.replicates * (config.retries + 1)
                input_total += billable_input * multiplier
                if role.provider == "fake":
                    continue
                if role.input_per_million is None or role.output_per_million is None:
                    condition_unknown = True
                    unknown = True
                else:
                    maximum += (
                        (billable_input * role.input_per_million + role.max_output_tokens * role.output_per_million)
                        / 1e6
                    ) * multiplier
        if not condition_unknown:
            total += maximum
        rows.append(
            {
                "condition": condition,
                "assistant_calls_per_episode_max": assistants,
                "judge_calls_per_episode_max": judges,
                "logical_calls_max": (assistants + judges) * len(episodes) * config.replicates,
                "completion_attempts_max": (assistants + judges)
                * len(episodes)
                * config.replicates
                * (config.retries + 1),
                "input_tokens_upper_bound": input_total,
                "max_input_tokens_per_call": largest,
                "estimated_usd_upper_bound": None if condition_unknown else maximum,
            }
        )
    return {
        "config_hash": digest(config.model_dump()),
        "episode_hash": digest([e.model_dump() for e in episodes]),
        "prompts": prompt_hashes(config.prompt_version),
        "episodes": len(episodes),
        "replicates": config.replicates,
        "conditions": rows,
        "estimated_usd_upper_bound": None if unknown else total,
        "budget_usd": config.budget_usd,
        "completion_requests_sent": 0,
        "context_warnings": warnings,
        "notes": [
            "No report cache savings assumed; deployment report cost attributed to each method.",
            "Exact source bytes, native output schemas and validated serialized report/excerpt/answer caps bound inputs.",
            "Every attempt reserves byte-bounded input plus configured output. Unknown billing is null.",
            "Oversized inputs are rejected before calls; live plans with context warnings are forbidden.",
            "Provider billing may exceed local estimates; use provider spending caps as well.",
        ],
    }


def check_live_plan(expansion, config):
    """Require a known budget, hosted roles and enough context for bounded calls."""
    cost = expansion["estimated_usd_upper_bound"]
    if config.budget_usd is None or cost is None or cost > config.budget_usd:
        raise ValueError("Live plan must have a known estimated maximum within explicit budget")
    for name in ["assistant", "judge"]:
        if config.roles[name].provider == "fake":
            raise ValueError("Live detector roles must explicitly configure hosted providers/models")
    if any(warning["provider"] != "fake" for warning in expansion.get("context_warnings", [])):
        raise ValueError(
            "Live plan exceeds configured context limits; increase reviewed context limits before inference"
        )
