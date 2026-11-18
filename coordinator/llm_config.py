"""
Load and validate config/llm.yaml: which model chain each role uses, output caps per call, and
how the debate's token limit is divided between the agents.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

DEFAULT_LLM_PATH = Path(__file__).resolve().parent.parent / "config" / "llm.yaml"
ROLES = ("charlie", "delta", "gamma", "vega", "sigma", "chat")
DEBATERS = ("charlie", "delta", "gamma", "vega")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelSpec(_Strict):
    provider: Literal["groq", "gemini", "nvidia", "openrouter"]
    model: str = Field(min_length=1)
    reasoning: Optional[Literal["low", "medium", "high"]] = None
    thinking_budget: Optional[int] = Field(default=None, ge=0)
    thinking_level: Optional[Literal["minimal", "low", "medium", "high"]] = None

    @model_validator(mode="after")
    def _fits_provider(self):
        if self.provider == "groq" and (self.thinking_budget is not None or self.thinking_level):
            raise ValueError(f"{self.model}: thinking_budget/thinking_level are gemini settings")
        if self.provider in ("nvidia", "openrouter") and (self.thinking_budget is not None or self.thinking_level):
            raise ValueError(f"{self.model}: thinking_budget/thinking_level are gemini settings")
        if self.provider == "gemini" and self.reasoning:
            raise ValueError(f"{self.model}: reasoning is a groq/nvidia setting; use thinking_budget or thinking_level")
        if self.thinking_budget is not None and self.thinking_level:
            raise ValueError(f"{self.model}: set thinking_budget or thinking_level, not both")
        return self

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


class MaxOutputTokens(_Strict):
    round1: int = Field(gt=0)
    round2: int = Field(gt=0)
    round3: int = Field(gt=0)
    narrative: int = Field(gt=0)

    def for_round(self, round_number: int) -> int:
        return getattr(self, f"round{round_number}")


class BudgetConfig(_Strict):
    shares: Dict[Literal["charlie", "delta", "gamma", "vega"], float]

    @model_validator(mode="after")
    def _valid_shares(self):
        missing = set(DEBATERS) - set(self.shares)
        if missing:
            raise ValueError(f"budget.shares missing: {sorted(missing)}")
        if any(v <= 0 for v in self.shares.values()):
            raise ValueError("budget.shares must be positive")
        if sum(self.shares.values()) >= 1:
            raise ValueError("budget.shares must sum to less than 1 (the rest is the shared reserve)")
        return self

    @property
    def reserve(self) -> float:
        return 1 - sum(self.shares.values())


class LLMConfig(_Strict):
    roles: Dict[Literal["charlie", "delta", "gamma", "vega", "sigma", "chat"], List[ModelSpec]]
    max_output_tokens: MaxOutputTokens
    budget: BudgetConfig
    # Largest request (prompt + output cap, in tokens) a provider accepts at once. Groq's free tier
    # rejects any request above its 8,000 tokens-per-minute limit; Kai sizes the output cap to fit
    # and sends a request that cannot fit to the next model in the chain instead.
    request_token_limits: Dict[Literal["groq", "gemini", "nvidia", "openrouter"], int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _all_roles(self):
        missing = set(ROLES) - set(self.roles)
        if missing:
            raise ValueError(f"roles missing: {sorted(missing)}")
        empty = [r for r, chain in self.roles.items() if not chain]
        if empty:
            raise ValueError(f"roles with no model: {empty}")
        return self

    def chain(self, role: str) -> List[ModelSpec]:
        if role not in self.roles:
            raise ValueError(f"unknown LLM role {role!r}; expected one of {sorted(self.roles)}")
        return self.roles[role]


@lru_cache(maxsize=8)
def _load(path: str, mtime: float) -> LLMConfig:
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    try:
        return LLMConfig.model_validate(raw)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())
        raise ValueError(f"invalid LLM config {path}: {problems}") from None


def load_llm_config(path: Optional[str] = None) -> LLMConfig:
    """Read and validate config/llm.yaml (or KAI_LLM_CONFIG_PATH). Raises ValueError naming the bad key."""
    p = Path(path or os.getenv("KAI_LLM_CONFIG_PATH") or DEFAULT_LLM_PATH)
    if not p.exists():
        raise ValueError(f"LLM config file not found: {p}")
    return _load(str(p), p.stat().st_mtime)
