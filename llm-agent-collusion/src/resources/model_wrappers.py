"""
Model wrappers.

Hackathon standard: every model call goes through **OpenRouter** with one
``OPENROUTER_API_KEY``. The paper's model names (``gpt-4.1``, ``claude-3-7-sonnet-latest``,
``gemini-2.5-flash-preview-04-17``...) are accepted as aliases and mapped to OpenRouter ids
(see ``cda_types.MODEL_ALIASES``); any other OpenRouter id (``qwen/qwen3-14b``,
``mistralai/mistral-small-3.2-24b-instruct``...) works as-is. The paper's default,
``gpt-4.1``, resolves to ``openai/gpt-4.1``.

Set ``LLM_BASE_URL`` to point the client at another OpenAI-compatible server (a local
Ollama, a mock in tests). The original direct-SDK clients (OpenAI, Anthropic, Vertex AI)
are kept below behind ``LLM_PROVIDER=openai|anthropic|google`` for reproduction only; their
SDKs are optional extras and imported lazily.
"""

from abc import ABC, abstractmethod
from typing import Any, List, Optional, TypedDict
import logging
import os
import random
import re
import tempfile
import time

from openai import OpenAI, APIError, BadRequestError, RateLimitError
from tqdm import tqdm

from poseidon_compute import make_client, resolve_model

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class Message(TypedDict):
    role: str
    content: str


# Sprint addition (CAIAC AM team): OpenRouter caps new accounts at 20 requests per minute for each
# model and answers anything above that with a 429. Two fixes: (1) when LLM_MAX_RPM is set (the sprint
# runner sets it, see sprint/run_conditions.py --rpm), every process sharing LLM_RATE_DIR stays under that
# many requests per minute per model, together; unset = no limit, as in the starter. (2) A 429 now means
# "wait until the limit resets and try again" instead of failing the agent's turn and crashing the run.
class RequestsPerMinute:
    """Sliding-window cap on requests per model, shared by every process through a lock file."""

    def __init__(self, rpm: float, state_dir: str, window: float = 60.0):
        self.rpm = rpm
        self.window = window
        self.state_dir = state_dir
        os.makedirs(state_dir, exist_ok=True)

    def wait_for_slot(self, model: str) -> None:
        import fcntl  # Unix only, and only needed when a limit is set

        path = os.path.join(self.state_dir, re.sub(r"[^A-Za-z0-9_.-]", "_", model))
        while True:
            with open(path + ".lock", "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)  # released when the file closes
                now = time.time()
                try:
                    with open(path, encoding="utf-8") as f:
                        stamps = [float(x) for x in f.read().split()]
                except FileNotFoundError:
                    stamps = []
                stamps = [t for t in stamps if now - t < self.window]
                if len(stamps) < self.rpm:
                    stamps.append(now)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(" ".join(f"{t:.3f}" for t in stamps))
                    return
                wait = self.window - (now - min(stamps)) + 0.05
            time.sleep(wait)


def limiter_from_env() -> Optional[RequestsPerMinute]:
    rpm = float(os.getenv("LLM_MAX_RPM") or 0)
    if rpm <= 0:
        return None
    state_dir = os.getenv("LLM_RATE_DIR") or os.path.join(tempfile.gettempdir(), "llm_rate_limit")
    return RequestsPerMinute(rpm, state_dir)


def _find_key(obj: Any, key: str) -> Any:
    """Case-insensitive search for `key` anywhere in a nested dict (OpenRouter nests its rate headers)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() == key:
                return v
            found = _find_key(v, key)
            if found is not None:
                return found
    return None


def seconds_until_reset(error: RateLimitError) -> float:
    """How long to wait after a 429: until the reset time the server sent, else until the next minute."""
    reset = _find_key(getattr(error, "body", None), "x-ratelimit-reset")
    if reset is None and getattr(error, "response", None) is not None:
        reset = error.response.headers.get("x-ratelimit-reset")
    now = time.time()
    try:
        reset = float(reset)
        wait = (reset / 1000 if reset > 1e11 else reset) - now  # epoch milliseconds or seconds
    except (TypeError, ValueError):
        wait = 60 - now % 60
    return min(max(wait, 1.0), 65.0) + random.uniform(0.5, 3.0)


class ModelWrapper(ABC):
    """Abstract base class for model API wrappers."""

    def __init__(
        self,
        model_name: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        top_p: float = 1.0,
        max_retries: int = 3,
        initial_retry_delay: float = 1.0,
        **kwargs,
    ):
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.max_retries = max_retries
        self.initial_retry_delay = initial_retry_delay
        self.additional_params = kwargs

    @abstractmethod
    def generate(self, messages: List[Message]) -> Optional[str]:
        """Generate a response for a single prompt."""

    def batch_generate(self, messages_list: List[List[Message]]) -> List[Optional[str]]:
        return [self.generate(m) for m in tqdm(messages_list, desc="Batch Generation")]

    @classmethod
    def create(cls, model_name: str, **kwargs) -> "ModelWrapper":
        """Factory: OpenRouter unless LLM_PROVIDER selects a direct SDK."""
        provider = os.getenv("LLM_PROVIDER", "openrouter").lower()
        if provider == "openai":
            return OpenAIClient(model_name, **kwargs)
        if provider == "anthropic":
            return AnthropicClient(model_name, **kwargs)
        if provider == "google":
            return GoogleClient(model_name, **kwargs)
        return OpenRouterClient(model_name, **kwargs)

    def _exponential_backoff(self, attempt: int) -> None:
        if attempt < self.max_retries:
            time.sleep(self.initial_retry_delay * (2**attempt))


class OpenRouterClient(ModelWrapper):
    """
    Default client. ``model_name`` must already be an OpenRouter id (use
    ``cda_types.resolve_model`` for the paper's aliases).

    If a model rejects ``response_format={"type": "json_object"}`` the request is retried
    without it (the prompts already demand JSON-only output).
    """

    is_remote = True
    max_rate_limit_waits = 60  # sprint addition: give up only after this many 429s in one call

    def __init__(self, model_name: str, **kwargs):
        super().__init__(model_name, **kwargs)
        self.model_name = resolve_model(model_name)
        self.client = make_client(timeout=120)
        self.is_remote = "localhost" not in str(self.client.base_url) and "127.0.0.1" not in str(self.client.base_url)
        self.limiter = limiter_from_env()

    def generate(self, messages: List[Message]) -> Optional[str]:
        params = dict(self.additional_params)
        attempt = 0
        rate_limited = 0
        while attempt < self.max_retries:
            if self.limiter is not None:
                self.limiter.wait_for_slot(self.model_name)
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    top_p=self.top_p,
                    **params,
                )
                content = response.choices[0].message.content or ""
                if "</think>" in content:  # reasoning models: keep only the answer
                    content = content.rsplit("</think>", 1)[1]
                return content
            except RateLimitError as e:  # sprint addition: wait it out, it does not count as a failed attempt
                rate_limited += 1
                if rate_limited > self.max_rate_limit_waits:
                    logger.error(f"Still rate limited after {rate_limited - 1} waits: {e}")
                    return None
                wait = seconds_until_reset(e)
                logger.warning(f"{self.model_name} rate limited (429); waiting {wait:.0f}s before retrying")
                time.sleep(wait)
                continue
            except BadRequestError as e:
                if "response_format" in params and "response_format" in str(e).lower():
                    logger.warning(f"{self.model_name} rejected JSON mode; retrying without response_format")
                    params.pop("response_format")
                    self.additional_params.pop("response_format", None)
                    attempt += 1
                    continue
                logger.warning(f"OpenRouter API error (attempt {attempt + 1}/{self.max_retries}): {e}")
                if attempt == self.max_retries - 1:
                    return None
                self._exponential_backoff(attempt)
            except APIError as e:
                logger.warning(f"OpenRouter API error (attempt {attempt + 1}/{self.max_retries}): {e}")
                if attempt == self.max_retries - 1:
                    logger.error(f"Failed after {self.max_retries} attempts: {e}")
                    return None
                self._exponential_backoff(attempt)
            attempt += 1
        return None


# --------------------------------------------------------------------------- #
# Original direct-SDK clients (opt-in via LLM_PROVIDER; not used in the hackathon)
# --------------------------------------------------------------------------- #
class OpenAIClient(ModelWrapper):
    def __init__(self, model_name: str, **kwargs):
        super().__init__(model_name, **kwargs)
        self.client = OpenAI(timeout=60)

    def generate(self, messages: List[Message]) -> Optional[str]:
        for attempt in range(self.max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    temperature=self.temperature,
                    max_completion_tokens=self.max_tokens,
                    top_p=self.top_p,
                    **self.additional_params,
                )
                return response.choices[0].message.content
            except APIError as e:
                logger.warning(f"OpenAI API error (attempt {attempt + 1}/{self.max_retries}): {e}")
                if attempt == self.max_retries - 1:
                    return None
                self._exponential_backoff(attempt)
        return None


class AnthropicClient(ModelWrapper):
    def __init__(self, model_name: str, **kwargs):
        super().__init__(model_name, **kwargs)
        from anthropic import Anthropic  # optional extra

        self.client = Anthropic(timeout=60)

    def generate(self, messages: List[Message]) -> Optional[str]:
        from anthropic import APIConnectionError, APIStatusError, RateLimitError

        if messages[0]["role"] == "system":
            system, messages = messages[0]["content"], messages[1:]
        else:
            system = "You are a helpful assistant."
        for attempt in range(self.max_retries):
            try:
                response = self.client.messages.create(
                    model=self.model_name,
                    system=system,
                    messages=messages,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    top_p=self.top_p,
                    **self.additional_params,
                )
                return response.content[0].text
            except (APIConnectionError, RateLimitError, APIStatusError) as e:
                logger.warning(f"Anthropic API error (attempt {attempt + 1}/{self.max_retries}): {e}")
                if attempt == self.max_retries - 1:
                    return None
                self._exponential_backoff(attempt)
        return None


class GoogleClient(ModelWrapper):
    def __init__(self, model_name: str, project: Optional[str] = None, location: str = "us-central1", **kwargs):
        super().__init__(model_name, **kwargs)
        import vertexai  # optional extra
        from vertexai.generative_models import GenerativeModel

        project = project or os.getenv("GOOGLE_PROJECT_ID")
        if project is None:
            raise ValueError("GOOGLE_PROJECT_ID must be set for LLM_PROVIDER=google")
        vertexai.init(project=project, location=location)
        self.model = GenerativeModel(model_name=self.model_name)

    def generate(self, messages: List[Message]) -> Optional[str]:
        from vertexai.generative_models import Content, GenerationConfig, Part

        if messages and messages[0]["role"] == "system":
            messages = messages[1:]
        contents = [
            Content(role="model" if m["role"] == "assistant" else "user", parts=[Part.from_text(m["content"])])
            for m in messages
            if m["role"] in ("user", "assistant")
        ]
        config = GenerationConfig(temperature=self.temperature, max_output_tokens=65535, top_p=self.top_p)
        for attempt in range(self.max_retries):
            try:
                response = self.model.generate_content(contents=contents, generation_config=config)
                if response.candidates and response.candidates[0].content.parts:
                    return response.candidates[0].content.parts[0].text
                return None
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Google API error (attempt {attempt + 1}/{self.max_retries}): {e}")
                if attempt == self.max_retries - 1:
                    return None
                self._exponential_backoff(attempt)
        return None
