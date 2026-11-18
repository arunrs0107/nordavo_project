"""LLM access. Two interchangeable backends with the same interface:

  Claude              Anthropic API, one forced tool call = one structured answer
  OpenAICompatible    any provider with an OpenAI-style /chat/completions endpoint
                      (Google Gemini free tier, Groq, OpenRouter, a local Ollama...)
                      The model is told to answer with ONE JSON object, which we parse.

Both raise only two kinds of errors the rest of the code cares about:
  LLMFatal        wrong key, unknown model, no credit: STOP the run (never post guesses)
  LLMUnavailable  rate limit / outage that outlasted our retries: skip the ticket, leave it open
Any other failure (e.g. unparseable output) is handled upstream as 'hand off to a human'.
"""
import json
import random
import re
import time
from dataclasses import dataclass
from typing import Callable, Optional


class LLMFatal(Exception):
    pass


class LLMUnavailable(Exception):
    pass


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.calls += other.calls


class Claude:
    def __init__(self, model: str, client=None):
        if client is None:
            import anthropic  # lazy import
            client = anthropic.Anthropic(max_retries=5)  # SDK retries 429/5xx with backoff
        self.client = client
        self.model = model
        self.usage = Usage()

    def call_tool(self, system: str, user: str, tool: dict, max_tokens: int = 1500) -> dict:
        try:
            resp = self.client.messages.create(
                model=self.model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": user}],
                tools=[tool], tool_choice={"type": "tool", "name": tool["name"]},
            )
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status in (400, 401, 403, 404):
                raise LLMFatal(f"Anthropic rejected the request ({status}): {str(exc)[:200]}") from exc
            if status is not None or exc.__class__.__name__.startswith("API"):
                raise LLMUnavailable(f"Anthropic unavailable: {exc.__class__.__name__}") from exc
            raise
        self.usage.add(Usage(resp.usage.input_tokens, resp.usage.output_tokens, 1))
        for block in resp.content:
            if block.type == "tool_use" and block.name == tool["name"]:
                return dict(block.input)
        raise ValueError("Model did not return the expected tool call")


def extract_json(text: str) -> dict:
    """Pull one JSON object out of a model answer (handles ```json fences and chatter)."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in model answer")
        value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("model answer is not a JSON object")
    return value


class OpenAICompatible:
    def __init__(self, model: str, base_url: str, api_key: str = "", min_interval: float = 4.0,
                 max_attempts: int = 6, session=None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        import requests
        self.model = model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.headers = {"Content-Type": "application/json"}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"
        self.min_interval = min_interval  # free tiers allow only a few requests per minute
        self.max_attempts = max_attempts
        self.session = session or requests.Session()
        self._sleep, self._clock, self._last = sleep, clock, -1e9
        self.usage = Usage()
        self._requests = requests

    def _post(self, payload: dict) -> dict:
        problem = ""
        for attempt in range(1, self.max_attempts + 1):
            wait = self.min_interval - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = self._clock()
            try:
                resp = self.session.post(self.url, headers=self.headers, json=payload, timeout=90)
            except (self._requests.ConnectionError, self._requests.Timeout) as exc:
                problem = f"network error {exc.__class__.__name__}"
                self._sleep(min(30, 2 ** attempt) * random.uniform(0.5, 1.5))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                problem = f"HTTP {resp.status_code}"
                try:
                    delay = float(resp.headers.get("Retry-After", 0))
                except ValueError:
                    delay = 0
                self._sleep(max(delay, min(30, 2 ** attempt)) * random.uniform(1.0, 1.3))
                continue
            if resp.status_code >= 400:
                raise LLMFatal(f"Model API rejected the request ({resp.status_code}): {resp.text[:200]}")
            return resp.json()
        raise LLMUnavailable(f"Model API still failing after {self.max_attempts} attempts ({problem})")

    def call_tool(self, system: str, user: str, tool: dict, max_tokens: int = 4000) -> dict:
        instructions = (
            system
            + "\n\nOUTPUT FORMAT (overrides any instruction to call a tool): reply with ONE JSON object and "
              "nothing else: no markdown fences, no commentary. It must follow this JSON schema, using the "
              "property names exactly as given:\n" + json.dumps(tool["input_schema"])
        )
        payload = {"model": self.model, "max_tokens": max_tokens, "temperature": 0,
                   "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": user}]}
        last_error: Optional[Exception] = None
        for _ in range(2):  # one retry if the answer is not valid JSON
            data = self._post(payload)
            usage = data.get("usage") or {}
            self.usage.add(Usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), 1))
            try:
                return extract_json(data["choices"][0]["message"]["content"] or "")
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                last_error = exc
        raise ValueError(f"model did not return valid JSON: {last_error}")


def make_llm(settings):
    if settings.provider == "anthropic":
        return Claude(settings.model)
    return OpenAICompatible(settings.model, settings.llm_base_url, settings.llm_api_key,
                            min_interval=settings.llm_min_interval)
