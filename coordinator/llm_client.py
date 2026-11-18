"""
LLM client for Kai: one client per role (charlie, delta, gamma, sigma, chat), each with its own
chain of models from config/llm.yaml, tried in order until one answers.

Token control (measured on real debates, see FIXES.md "Phase 6"):
  * hidden reasoning is the largest cost, so reasoning effort / thinking budget is set per model
  * every call has an output cap, so one runaway reply cannot exhaust the debate's budget
  * a request is sized to the provider's per-request limit (Groq free: 8,000 tokens per minute);
    one that cannot fit goes to the next model instead of being rejected
  * on a 429 the provider's retry-after is honoured before falling through, so a per-minute
    limit does not burn the next model's daily quota
  * when a JSON schema is given, providers are asked to enforce it (strict mode), which makes
    replies citing unknown ids or using unknown verbs impossible instead of retried
Usage is reported split into prompt, output, hidden reasoning and cached prompt tokens.
"""

import argparse
import json
import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

import httpx
from dotenv import load_dotenv

from coordinator.llm_config import LLMConfig, ModelSpec, load_llm_config

load_dotenv()

# A provider call that has not answered in this time fails over to the next model (seen live:
# a Gemini request with no timeout hung a test run indefinitely).
REQUEST_TIMEOUT_S = 120

# Longest pause taken after a rate limit before moving on (Groq's per-minute window is 60 s).
MAX_RATE_LIMIT_WAIT_S = 60

NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
KEY_ENV = {"groq": "GROQ_API_KEY", "gemini": "GOOGLE_API_KEY", "nvidia": "NVIDIA_API_KEY",
           "openrouter": "OPENROUTER_API_KEY"}


class Usage:
    """Token usage of one reply. Attribute names follow the response objects the rest of Kai reads."""

    def __init__(self, prompt: int = 0, output: int = 0, reasoning: int = 0, cached: int = 0,
                 total: Optional[int] = None):
        self.prompt_token_count = prompt
        self.candidates_token_count = output          # visible output
        self.thoughts_token_count = reasoning         # hidden reasoning, billed as output
        self.cached_content_token_count = cached      # part of the prompt served from cache
        self.total_token_count = total if total is not None else prompt + output + reasoning


@dataclass
class LLMResponse:
    text: str
    usage_metadata: Usage
    model: str                                        # "provider:model" that answered
    fallbacks: List[str] = field(default_factory=list)   # models that failed before it


def _retry_delay(failures: List[str]) -> Optional[float]:
    """The longest wait a provider asked for ("retryDelay": "23s", "try again in 7.5s"), if any."""
    waits = [float(x) for f in failures for x in re.findall(
        r"(?:retryDelay['\"]?:\s*['\"]|try again in\s*|retry-after\s*)(\d+(?:\.\d+)?)s", f)]
    return max(waits) if waits else None


_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")


def _parse_duration(text: str) -> Optional[float]:
    """Seconds in a header value such as "7.5", "7.66s", "1m30s" or "250ms"."""
    text = (text or "").strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)
    parts = _DURATION_PART.findall(text)
    if not parts:
        return None
    unit = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}
    return sum(float(n) * unit[u] for n, u in parts)


def _header_wait(exc: Exception) -> Optional[float]:
    """Seconds a provider asked us to wait, from the error's response headers: `retry-after`,
    else Groq's `x-ratelimit-reset-tokens` (the per-minute window). None when not given."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if not headers:
        return None
    for name in ("retry-after", "x-ratelimit-reset-tokens"):
        wait = _parse_duration(headers.get(name, ""))
        if wait is not None:
            return wait
    return None


class ProviderHTTPError(RuntimeError):
    """A failed NVIDIA call; keeps the response so retry-after can be read."""

    def __init__(self, message: str, response: Optional[httpx.Response] = None):
        super().__init__(message)
        self.response = response


def _transient(message: str) -> bool:
    """Rate limits and temporary overloads: worth retrying the chain after a pause."""
    m = message.lower()
    if "per day" in m or "perday" in m or "per-day" in m or "daily" in m:
        return False          # a daily quota does not recover by waiting a minute
    return any(k in m for k in ("429", "rate limit", "rate_limit", "503", "unavailable", "overloaded",
                                "high demand", "502", "timeout", "timed out"))


class MultiLLMClient:
    """
    Calls the role's model chain in order. A model that errors (rate limit, retired id, bad
    request) is skipped and the next one is tried; if a whole pass fails only on rate limits or
    temporary overloads (429/503), the chain is retried after a short backoff.
    """

    def __init__(self, system_instruction: str, role: str = "charlie",
                 config: Optional[LLMConfig] = None, groq_client: Any = None, gemini_client: Any = None):
        self.system_instruction = system_instruction
        self.role = role
        self.config = config or load_llm_config()
        self.chain: List[ModelSpec] = self.config.chain(role)
        self._groq = groq_client
        self._gemini = gemini_client

    # ------------------------------------------------------------------ providers

    def _groq_client(self):
        if self._groq is None and os.getenv("GROQ_API_KEY"):
            from groq import Groq
            self._groq = Groq(api_key=os.getenv("GROQ_API_KEY"), timeout=REQUEST_TIMEOUT_S, max_retries=0)
        return self._groq

    def _gemini_client(self):
        if self._gemini is None and os.getenv("GOOGLE_API_KEY"):
            from google import genai
            from google.genai import types
            # v1beta: system instructions and JSON schemas are not enabled on v1 for every model
            self._gemini = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"),
                                        http_options=types.HttpOptions(api_version="v1beta",
                                                                       timeout=REQUEST_TIMEOUT_S * 1000))
        return self._gemini

    def _call_groq(self, client, spec: ModelSpec, prompt: str, schema: Optional[Dict],
                   schema_name: str, max_output_tokens: Optional[int]) -> LLMResponse:
        kwargs: Dict[str, Any] = {
            "model": spec.model,
            "messages": [{"role": "system", "content": self.system_instruction},
                         {"role": "user", "content": prompt}],
        }
        if spec.reasoning:
            kwargs["reasoning_effort"] = spec.reasoning
        if max_output_tokens:
            kwargs["max_completion_tokens"] = max_output_tokens
        if schema:
            kwargs["response_format"] = {"type": "json_schema", "json_schema": {
                "name": schema_name, "strict": True, "schema": schema}}
        r = client.chat.completions.create(**kwargs)
        u = r.usage
        reasoning = getattr(getattr(u, "completion_tokens_details", None), "reasoning_tokens", 0) or 0
        cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        completion = u.completion_tokens or 0
        usage = Usage(prompt=u.prompt_tokens or 0, output=max(completion - reasoning, 0),
                      reasoning=reasoning, cached=cached, total=u.total_tokens)
        return LLMResponse(r.choices[0].message.content or "", usage, spec.label)

    def _call_gemini(self, client, spec: ModelSpec, prompt: str, schema: Optional[Dict],
                     max_output_tokens: Optional[int]) -> LLMResponse:
        from google.genai import types
        cfg: Dict[str, Any] = {"system_instruction": self.system_instruction}
        if max_output_tokens:
            cfg["max_output_tokens"] = max_output_tokens
        if spec.thinking_budget is not None:
            cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=spec.thinking_budget)
        elif spec.thinking_level:
            cfg["thinking_config"] = types.ThinkingConfig(thinking_level=spec.thinking_level)
        if schema:
            cfg["response_mime_type"] = "application/json"
            cfg["response_json_schema"] = schema
        r = client.models.generate_content(model=spec.model, contents=prompt,
                                           config=types.GenerateContentConfig(**cfg))
        m = r.usage_metadata
        usage = Usage(prompt=getattr(m, "prompt_token_count", 0) or 0,
                      output=getattr(m, "candidates_token_count", 0) or 0,
                      reasoning=getattr(m, "thoughts_token_count", 0) or 0,
                      cached=getattr(m, "cached_content_token_count", 0) or 0,
                      total=getattr(m, "total_token_count", None))
        return LLMResponse(r.text or "", usage, spec.label)

    # ------------------------------------------------------------------ public

    def estimate_tokens(self, text: str) -> int:
        """Conservative prompt-size estimate used to size the output cap before a call. Measured
        on a live debate: 2.6 characters per token on Groq (the schema is billed as prompt),
        3.2 or more on Gemini."""
        return int(len(self.system_instruction + text) / 2.5) + 1

    def _call_nvidia(self, key: str, spec: ModelSpec, prompt: str, schema: Optional[Dict],
                     schema_name: str, max_output_tokens: Optional[int]) -> LLMResponse:
        """NVIDIA's hosted API catalog (build.nvidia.com), OpenAI-compatible, plain HTTP.

        No response_format / guided_json is sent: the hosted endpoint does not enforce a schema
        reliably (it may ignore it or reject the request). Kai's prompts already spell out the
        JSON shape and every reply is validated and repaired in code (agents/schema.py).
        `reasoning` set in llm.yaml turns the model's thinking mode on; unset keeps it off, so the
        output cap is not spent on hidden reasoning."""
        body: Dict[str, Any] = {
            "model": spec.model,
            "messages": [{"role": "system", "content": self.system_instruction},
                         {"role": "user", "content": prompt + ("\n\nReply with ONLY the JSON object." if schema else "")}],
        }
        if spec.provider == "nvidia":
            body["chat_template_kwargs"] = {"enable_thinking": bool(spec.reasoning)}
        if max_output_tokens:
            body["max_tokens"] = max_output_tokens
        r = httpx.post(OPENROUTER_URL if spec.provider == "openrouter" else NVIDIA_URL, json=body, timeout=REQUEST_TIMEOUT_S,
                       headers={"Authorization": f"Bearer {key}"})
        try:
            data = r.json() if r.content else {}
        except ValueError:
            data = {"detail": r.text[:300]}
        if r.status_code >= 400 or data.get("error"):
            detail = json.dumps(data.get("error") or data)[:400]
            raise ProviderHTTPError(f"{spec.provider} {r.status_code}: {detail}", r)
        u = data.get("usage") or {}
        reasoning = (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0
        completion = u.get("completion_tokens", 0) or 0
        usage = Usage(prompt=u.get("prompt_tokens", 0) or 0, output=max(completion - reasoning, 0),
                      reasoning=reasoning, total=u.get("total_tokens"))
        return LLMResponse(data["choices"][0]["message"].get("content") or "", usage, spec.label)

    def _client_for(self, spec: ModelSpec):
        if spec.provider == "groq":
            return self._groq_client()
        if spec.provider == "gemini":
            return self._gemini_client()
        return os.getenv(KEY_ENV[spec.provider]) or None

    def _fit_request(self, spec: ModelSpec, prompt: str, schema: Optional[Dict],
                     max_output_tokens: Optional[int]):
        """Size a request to the provider's per-request limit (llm.yaml request_token_limits).
        Returns (output cap, None), or (None, reason) when the prompt alone leaves too little
        room for a useful reply: the call is skipped so the next model answers."""
        limit = self.config.request_token_limits.get(spec.provider)
        if not limit:
            return max_output_tokens, None
        prompt_tokens = self.estimate_tokens(prompt + (json.dumps(schema) if schema else ""))
        room = limit - prompt_tokens
        wanted = max_output_tokens or 1000
        if room >= wanted:
            return max_output_tokens, None
        if room < max(500, wanted // 2):
            return None, (f"{spec.label}: skipped, request ~{prompt_tokens} tokens leaves {max(room, 0)} "
                          f"for the reply under the {limit}-token limit")
        return room, None

    def _call(self, spec: ModelSpec, client, prompt, schema, schema_name, cap) -> LLMResponse:
        if spec.provider == "groq":
            return self._call_groq(client, spec, prompt, schema, schema_name, cap)
        if spec.provider == "gemini":
            return self._call_gemini(client, spec, prompt, schema, cap)
        return self._call_nvidia(client, spec, prompt, schema, schema_name, cap)

    def generate_content(self, prompt: str, max_retries: int = 3, *, schema: Optional[Dict] = None,
                         schema_name: str = "reply", max_output_tokens: Optional[int] = None,
                         exclude: Optional[Iterable[str]] = None) -> LLMResponse:
        """Answer from the first model in the chain that succeeds. Raises RuntimeError naming
        every model's failure when none does. `exclude` lists "provider:model" labels to skip
        (used to ask a different model after one returned an unusable reply); if that would
        leave nothing, the whole chain is used."""
        failures: List[str] = []
        skip = set(exclude or ())
        chain = [m for m in self.chain if m.label not in skip] or self.chain
        for attempt in range(max_retries):
            rate_limited_only = True
            for spec in chain:
                try:
                    client = self._client_for(spec)
                except ImportError as e:   # SDK not installed: skip this model, keep the chain going
                    failures.append(f"{spec.label}: provider SDK not installed ({e}); "
                                    "run pip install -r requirements.txt")
                    rate_limited_only = False
                    continue
                if client is None:
                    failures.append(f"{spec.label}: no {KEY_ENV[spec.provider]}")
                    rate_limited_only = False
                    continue
                cap, skipped = self._fit_request(spec, prompt, schema, max_output_tokens)
                if skipped:
                    failures.append(skipped)
                    print(f"[LLM] {self.role}: {skipped}")
                    continue
                waited = False
                while True:
                    try:
                        resp = self._call(spec, client, prompt, schema, schema_name, cap)
                        resp.fallbacks = list(failures)
                        return resp
                    except Exception as e:  # provider error: wait if asked, else try the next model
                        msg = str(e)
                        wait = _header_wait(e) or _retry_delay([msg])
                        if wait:
                            msg += f" [retry-after {wait:g}s]"
                        transient = _transient(msg)
                        if transient and wait and not waited and wait <= MAX_RATE_LIMIT_WAIT_S and "429" in msg:
                            # A per-minute limit clears within the window: wait it out and ask the
                            # same model again, instead of spending the next model's daily quota.
                            waited = True
                            print(f"[LLM] {self.role}: {spec.label} rate-limited; waiting {wait:.0f}s as asked")
                            time.sleep(wait + random.uniform(0.5, 1.5))
                            continue
                        failures.append(f"{spec.label}: {msg[:600]}")
                        print(f"[LLM] {self.role}: {spec.label} failed ({msg[:120]}); trying next model")
                        if not transient:
                            rate_limited_only = False
                        break
            if not rate_limited_only or attempt == max_retries - 1:
                break
            wait = _retry_delay(failures) or 5 * (2 ** attempt)
            print(f"[LLM] {self.role}: every model rate-limited or overloaded; retrying in {wait:.0f}s")
            time.sleep(min(wait, MAX_RATE_LIMIT_WAIT_S) + random.uniform(0, 1))
        if not failures:
            raise ValueError("No LLM configured: set GROQ_API_KEY, GOOGLE_API_KEY and/or NVIDIA_API_KEY.")
        raise RuntimeError(f"all models failed for {self.role}: " + " | ".join(failures))


def _list_models():
    """Print the model ids the configured keys can use (no tokens are spent)."""
    if os.getenv("GROQ_API_KEY"):
        from groq import Groq
        print("groq:", ", ".join(sorted(m.id for m in Groq().models.list().data)))
    if os.getenv("GOOGLE_API_KEY"):
        from google import genai
        names = sorted(m.name.removeprefix("models/") for m in genai.Client().models.list()
                       if "generateContent" in (m.supported_actions or []))
        print("gemini:", ", ".join(names))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Kai LLM client")
    ap.add_argument("--list", action="store_true", help="list models available to the configured keys")
    if ap.parse_args().list:
        _list_models()
