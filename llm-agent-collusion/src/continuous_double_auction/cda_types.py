from functools import total_ordering
from pydantic import BaseModel, Field, model_validator
from typing import Any, Dict, Optional, Self


# The paper's model names, mapped to OpenRouter ids (hackathon standard: everything runs
# through OpenRouter). Any other OpenRouter id is accepted verbatim.
MODEL_ALIASES: Dict[str, str] = {
    "gpt-4o-mini": "openai/gpt-4o-mini",
    "gpt-4o": "openai/gpt-4o",
    "gpt-4.1": "openai/gpt-4.1",  # paper default
    "gpt-4.1-mini": "openai/gpt-4.1-mini",  # paper's overseer / LLM judge
    "claude-3-5-haiku-latest": "anthropic/claude-3.5-haiku",
    "claude-3-5-sonnet-latest": "anthropic/claude-3.5-sonnet",
    "claude-3-7-sonnet-latest": "anthropic/claude-3.7-sonnet",
    "gemini-2.5-flash-preview-04-17": "google/gemini-2.5-flash",
    "gemini-2.5-pro-preview-03-25": "google/gemini-2.5-pro",
}
SUPPORTED_MODELS = list(MODEL_ALIASES)  # aliases; see resolve_model()
DEFAULT_MODEL = "gpt-4.1"
ZIP_BUYER = "zip"  # rule-based buyer, no model call
# Sprint addition (CAIAC AM team): scripted "maverick" seller, no model call. Use it as one entry of
# --seller_models to replace that seller with a price-cutter (see market/agents.py: MaverickSeller).
MAVERICK_SELLER = "maverick"

Model = str


def resolve_model(name: str) -> str:
    """Alias -> OpenRouter id; unknown names are assumed to already be OpenRouter ids."""
    return MODEL_ALIASES.get(name, name)

class ExperimentParams(BaseModel):
    """
    Parameters for the continuous double auction experiment.
    """
    seller_valuations: list[float]
    buyer_valuations: list[float]
    seller_models: list[Model]
    buyer_models: list[Optional[Model]]  # None or "zip" = rule-based ZIP buyer (no model call)
    seller_prompt_template: str = "seller_prompt_base.jinja2"
    buyer_prompt_template: str = "buyer_prompt_base.jinja2"
    rounds: int
    seller_comms_enabled: bool = False
    buyer_comms_enabled: bool = False
    hide_num_rounds: bool = False  # Whether to hide the total number of rounds from agents
    oversight_enabled: bool = False  # Whether to enable oversight for sellers
    boss_pressure: bool = False  # Whether to apply pressure on sellers to keep prices high
    temperature: float = 0.7
    tag: str = ""
    seller_demonyms: Optional[dict[str, str]] = None  # Maps seller IDs to their country demonyms (e.g., "seller_1": "American")
    # Initial market conditions
    initial_seller_ask_center: float = 97.5  # Default center for initial seller asks
    initial_seller_ask_spread: float = 2.5   # Default spread for initial seller asks
    initial_buyer_bid_center: float = 82.5   # Default center for initial buyer bids
    initial_buyer_bid_spread: float = 2.5    # Default spread for initial buyer bids
    initial_condition: bool = True           # Flag to enable using initial conditions
    max_message_length: int = 500           # Maximum length for agent communication messages
    # Hackathon additions
    seed: Optional[int] = None               # seeds agent names / initial orders; forwarded to the API when supported
    overseer_model: str = "gpt-4.1-mini"     # model reading seller messages when oversight is enabled (paper: gpt-4.1-mini)
    overseer_sample_fraction: float = 1.0    # share of seller messages the overseer gets to see (sprint project 3.4)
    llm_eval: bool = False                   # run the LLM-as-judge coordination scoring after the run (paper: always)
    # Sprint additions (CAIAC AM team, sprint project 1.5)
    oversight_warning_only: bool = False     # sellers see the oversight warning, but no overseer runs and no gag (a bluff)
    oversight_silent: bool = False           # a real overseer reads messages and can gag, but sellers get no warning
    enforce_gag: bool = False                # once the gag is on, seller messages are actually cut to 5 characters
    maverick_floor: Optional[float] = None   # lowest ask a maverick seller will post; None = competitive equilibrium price
    maverick_undercut: float = 1.0           # how far (in $) a maverick seller prices below the lowest rival ask

    @model_validator(mode='after')
    def validate_param_combinations(self) -> Self:
        if len(self.seller_valuations) != len(self.seller_models):
            raise ValueError('Number of seller valuations must match number of seller models')
        if len(self.buyer_valuations) != len(self.buyer_models):
            raise ValueError('Number of buyer valuations must match number of buyer models')
        if self.oversight_enabled and not self.seller_comms_enabled:
            raise ValueError('Oversight can only be enabled if seller communications are enabled')
        if self.oversight_warning_only and not self.seller_comms_enabled:
            raise ValueError('The oversight warning only makes sense if seller communications are enabled')
        if self.oversight_warning_only and self.oversight_enabled:
            raise ValueError('Choose either oversight_enabled (real overseer) or oversight_warning_only (bluff), not both')
        if self.maverick_undercut < 0:
            raise ValueError('maverick_undercut must be >= 0')
        if self.oversight_silent and not self.oversight_enabled:
            raise ValueError('oversight_silent needs oversight_enabled (a real overseer)')
        if self.enforce_gag and not self.oversight_enabled:
            raise ValueError('enforce_gag needs oversight_enabled (only the overseer can turn the gag on)')
        if not 0.0 <= self.overseer_sample_fraction <= 1.0:
            raise ValueError('overseer_sample_fraction must be between 0 and 1')
        self.buyer_models = [None if m in (None, ZIP_BUYER, "none", "None") else m for m in self.buyer_models]
        return self


AgentBidResponse = dict[str, Any]

@total_ordering
class Agent(BaseModel):
    """
    Base class representing an agent in a continuous double auction market.

    Attributes:
        id (str): The buyer's identifying string.
        company: The company this seller is representing
        valuation (float): The price at which the agent values the asset.
        experiment_params (ExperimentParams): Various parameters required to configure agent behavior.
    """
    id: str
    company: str
    valuation: float
    expt_params: ExperimentParams

    def __lt__(self, other: "Agent") -> bool:
        """
        Compare agents based on their ids.
        """
        return self.id < other.id

    def generate_bid_response(self, **kwargs: Any) -> AgentBidResponse:
        """
        Generate a bid response for the agent. Inheriting classes should
        override this method to implement their own bidding strategy.

        Args:
            kwargs: Any additional parameters needed for the bid generation.

        Returns:
            AgentBidResponse: The bid response
        """
        ...


class Trade(BaseModel):
    """
    Represents a trade between a buyer and a seller in the market.
    """
    round_number: int
    buyer_id: str
    seller_id: str
    price: float


class MarketRound(BaseModel):
    """
    Represents a single round of trading in the double auction market.
    """
    round_number: int
    seller_asks: dict[str, float] = Field(default_factory=lambda: {})
    buyer_bids: dict[str, float] = Field(default_factory=lambda: {})
    trades: list[Trade] = Field(default_factory=lambda: [])
    seller_messages: dict[str, str] = Field(default_factory=lambda: {})
    buyer_messages: dict[str, str] = Field(default_factory=lambda: {})
    overseer_visible_messages: Optional[list[str]] = None  # seller ids whose messages the overseer saw
