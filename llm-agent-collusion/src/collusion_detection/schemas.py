"""Public evidence and monitor output contracts; no private labels in these types."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Participant(Strict):
    alias: str
    role: Literal["seller", "buyer"]
    valuation: float | None = None


class Event(Strict):
    event_id: str
    round: int = Field(ge=1)
    event_type: Literal["seller_message", "ask_record", "bid_record", "trade", "outcome"]
    actor: str | None = None
    text: str
    source_ref: str
    available_round: int | None = None
    # A round record cannot identify omitted response vs withdrawal vs retained queue order.
    observation: Literal["message", "recorded", "missing_or_withdrawn", "executed", "derived"]


class Episode(Strict):
    episode_id: str
    group_id: str
    participants: list[Participant]
    rounds: int = Field(ge=1)
    events: list[Event]
    oversight: Literal["disabled", "enabled", "unknown"]
    synthetic: bool = False

    @model_validator(mode="after")
    def unique_events(self):
        ids = [e.event_id for e in self.events]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate event IDs")
        return self


class Evidence(Strict):
    episode_id: str
    view: Literal["M", "MAO", "A"]
    context: dict
    events: list[Event]


AssessmentLevel = Literal["supported", "not_supported", "insufficient_evidence"]


class Assessment(Strict):
    implemented_collusion_probability: float = Field(ge=0, le=1)
    agreement: AssessmentLevel
    implementation: AssessmentLevel
    suspected_coalition: list[str] = Field(max_length=20)
    supporting_evidence_ids: list[str] = Field(max_length=40)
    counterevidence: list[str] = Field(max_length=20)
    explanation: str = Field(max_length=3000)
    limitations: list[str] = Field(max_length=20)


class Selection(Strict):
    event_id: str
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)


class Selections(Strict):
    selections: list[Selection] = Field(max_length=24)


class EventReference(Strict):
    """A source pointer; application code supplies the exact event text."""

    event_id: str


class EventSelections(Strict):
    selections: list[EventReference] = Field(max_length=24)


class Excerpt(Strict):
    event_id: str
    quote: str = Field(min_length=1, max_length=6000)


class Relationship(Strict):
    event_ids: list[str] = Field(min_length=1, max_length=12)
    description: str = Field(max_length=1000)


class LinkedReport(Strict):
    candidate_arrangement: str = Field(max_length=1500)
    sellers: list[str] = Field(max_length=20)
    support: list[Excerpt] = Field(max_length=24)
    relationships: list[Relationship] = Field(max_length=24)
    counterevidence: list[Excerpt] = Field(max_length=16)
    independent_explanations: list[str] = Field(max_length=12)
    limitations: list[str] = Field(max_length=12)


class Answer(Strict):
    answer: str = Field(max_length=3000)
    excerpts: list[Excerpt] = Field(max_length=12)
    limitations: list[str] = Field(max_length=12)


class ReferencedReport(LinkedReport):
    """v3 assistant contract: select evidence rather than regenerating quotes."""

    support: list[EventReference] = Field(max_length=24)
    counterevidence: list[EventReference] = Field(max_length=16)


class ReferencedAnswer(Answer):
    """v3 clarification contract with source pointers instead of quotations."""

    excerpts: list[EventReference] = Field(max_length=12)


class Decision(Strict):
    action: Literal["ASK", "FINISH"]
    question: str | None = Field(default=None, max_length=600)
    assessment: Assessment | None = None

    @model_validator(mode="after")
    def coherent(self):
        if self.action == "ASK" and (not self.question or self.assessment is not None):
            raise ValueError("ASK requires one question, no assessment")
        if self.action == "FINISH" and (self.assessment is None or self.question is not None):
            raise ValueError("FINISH requires assessment, no question")
        return self


class PrivateLabel(Strict):
    """Private annotation contract, never serialised into Evidence."""

    episode_id: str
    reviewer: str = Field(min_length=1)
    agreement: AssessmentLevel | None = None
    implemented_collusion: Literal["positive", "negative", "ambiguous"] | None = None
    coalition: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    economic_consequences: str | None = None
    public_disclosure: AssessmentLevel | None = None
    assigned_scenario: str | None = None
    assigned_pact: list[str] = Field(default_factory=list)
    adjudicator: str | None = None
    synthetic: bool = False
    review_records: list[dict] | None = None
