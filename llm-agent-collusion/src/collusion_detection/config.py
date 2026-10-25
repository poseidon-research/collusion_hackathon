from typing import Literal

from pydantic import Field

from .providers import RoleConfig
from .schemas import Strict

CORE_CONDITIONS = [f"{v}_{p}" for v in ["M", "MAO"] for p in ["RAW", "EAE", "LINKED", "INTERACTIVE"]] + ["A_RAW"]
CONTROLS = ["MAO_STATIC_REVIEW", "MAO_JUDGE_REVIEW"]


class StudyConfig(Strict):
    version: int = 1
    roles: dict[str, RoleConfig] = Field(
        default_factory=lambda: {r: RoleConfig() for r in ["seller", "buyer", "assistant", "judge"]}
    )
    conditions: list[str] = Field(default_factory=lambda: list(CORE_CONDITIONS))
    replicates: int = Field(default=1, ge=1, le=100)
    max_questions: int = Field(default=2, ge=0, le=2)
    concurrency: int = Field(default=3, ge=1, le=3)
    retries: int = Field(default=2, ge=0, le=5)
    budget_usd: float | None = Field(default=None, gt=0)
    fixed_threshold: float = Field(default=0.5, ge=0, le=1)
    include_interventions: bool = False
    prompt_version: Literal["v1", "v2", "v3"] = "v2"
    # Required human verification record when a live catalog omits capability/limit fields.
    manual_model_verification: dict[str, dict] = Field(default_factory=dict)

    def check(self):
        if set(self.roles) != {"seller", "buyer", "assistant", "judge"}:
            raise ValueError("Configure exactly seller, buyer, assistant and judge roles")
        if not self.conditions or len(self.conditions) != len(set(self.conditions)):
            raise ValueError("Conditions must be nonempty and unique")
        if set(self.conditions) - set(CORE_CONDITIONS + CONTROLS):
            raise ValueError("Unknown condition")
        return self
