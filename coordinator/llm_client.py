"""
LLM client for Kai: one client per role (charlie, delta, gamma, sigma, chat), each with its own
chain of models from config/llm.yaml, tried in order until one answers.

Token control (measured on real debates, see FIXES.md "Phase 6"):
  * hidden reasoning is the largest cost, so reasoning effort / thinking budget is set per model
  * every call has an output cap, so one runaway reply cannot exhaust the debate's budget
  * when a JSON schema is given, providers are asked to enforce it (strict mode), which makes
    replies citing unknown ids or using unknown verbs impossible instead of retried
Usage is reported split into prompt, output, hidden reasoning and cached prompt tokens.
"""

import argparse
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from coordinator.llm_config import LLMConfig, ModelSpec, load_llm_config

load_dotenv()

# A provider call that has not answered in this time fails over to the next model (seen live:
# a Gemini request with no timeout hung a test run indefinitely).
REQUEST_TIMEOUT_S = 120


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
    import re
    waits = [float(x) for f in failures for x in re.findall(r"(?:retryDelay['\"]?:\s*['\"]|try again in\s*)(\d+(?:\.\d+)?)s", f)]
    return max(waits) if waits else None


def _transient(message: str) -> bool:
    """Rate limits and temporary overloads: worth retrying the chain after a pause."""
    m = message.lower()
    if "per day" in m or "perday" in m or "daily" in m:
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

    def generate_content(self, prompt: str, max_retries: int = 3, *, schema: Optional[Dict] = None,
                         schema_name: str = "reply", max_output_tokens: Optional[int] = None) -> LLMResponse:
        """Answer from the first model in the chain that succeeds. Raises RuntimeError naming
        every model's failure when none does."""
        failures: List[str] = []
        for attempt in range(max_retries):
            rate_limited_only = True
            for spec in self.chain:
                try:
                    client = self._groq_client() if spec.provider == "groq" else self._gemini_client()
                except ImportError as e:   # SDK not installed: skip this model, keep the chain going
                    failures.append(f"{spec.label}: provider SDK not installed ({e}); "
                                    "run pip install -r requirements.txt")
                    rate_limited_only = False
                    continue
                if client is None:
                    failures.append(f"{spec.label}: no {'GROQ' if spec.provider == 'groq' else 'GOOGLE'}_API_KEY")
                    rate_limited_only = False
                    continue
                try:
                    if spec.provider == "groq":
                        resp = self._call_groq(client, spec, prompt, schema, schema_name, max_output_tokens)
                    else:
                        resp = self._call_gemini(client, spec, prompt, schema, max_output_tokens)
                    resp.fallbacks = list(failures)
                    return resp
                except Exception as e:  # provider error: try the next model in the chain
                    msg = str(e)
                    failures.append(f"{spec.label}: {msg[:600]}")
                    print(f"[LLM] {self.role}: {spec.label} failed ({msg[:120]}); trying next model")
                    if not _transient(msg):
                        rate_limited_only = False
            if not rate_limited_only or attempt == max_retries - 1:
                break
            wait = _retry_delay(failures) or 5 * (2 ** attempt)
            print(f"[LLM] {self.role}: every model rate-limited or overloaded; retrying in {wait:.0f}s")
            time.sleep(min(wait, 60) + random.uniform(0, 1))
        if not failures:
            raise ValueError("No LLM configured: set GROQ_API_KEY and/or GOOGLE_API_KEY.")
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
