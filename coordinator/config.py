"""
Load and validate config/thresholds.yaml (PHASE4_PLAN.md §3).

All tunable numbers for the judge, circuit breaker, CAPM and HITL live in that file so they can
be edited by hand. The path can be overridden with the KAI_THRESHOLDS_PATH environment variable.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Dict, Literal, Optional, Tuple

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "thresholds.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PersonaThresholds(_Strict):
    buy_score: float = Field(gt=0, le=1)
    min_margin_of_safety: float
    max_volatility: Optional[float] = Field(default=None, gt=0)
    sell_score: float = Field(ge=-1, lt=0)
    buy_requires_dcf: bool


class VerificationConfig(_Strict):
    numeric_tolerance: float = Field(gt=0, lt=1)
    min_checkable_claims: int = Field(ge=1)
    min_quote_chars: int = Field(ge=1)


class CircuitBreakerConfig(_Strict):
    min_agent_faithfulness: float = Field(ge=0, le=1)
    min_valid_agents: int = Field(ge=1, le=3)
    token_limit: int = Field(gt=0)


class JudgeConfig(_Strict):
    weights: Dict[Literal["charlie", "delta", "gamma", "vega"], float]

    @model_validator(mode="after")
    def _positive(self):
        if any(w <= 0 for w in self.weights.values()):
            raise ValueError("judge.weights must be positive")
        return self


class IntegrityConfig(_Strict):
    unexplained_stance_penalty: float = Field(ge=0, le=1)
    retained_failed_claim_penalty: float = Field(ge=0, le=1)


class CapmConfig(_Strict):
    equity_risk_premium: float = Field(gt=0, lt=0.2)
    beta_clamp: Tuple[float, float]
    rate_clamp: Tuple[float, float]
    fallback_rate: float = Field(gt=0, lt=0.5)
    terminal_growth: float = Field(ge=0, lt=0.1)

    @model_validator(mode="after")
    def _ordered(self):
        for name in ("beta_clamp", "rate_clamp"):
            lo, hi = getattr(self, name)
            if lo >= hi:
                raise ValueError(f"{name} must be [low, high] with low < high")
        if self.terminal_growth >= self.rate_clamp[0]:
            raise ValueError("terminal_growth must be below the lowest discount rate")
        return self


class HitlConfig(_Strict):
    approval_confidence: float = Field(ge=0, le=100)
    low_confidence_action: Literal["warn", "approve"] = "warn"
    approval_timeout_minutes: int = Field(gt=0)


class QuantReturnsConfig(_Strict):
    window_days: int = Field(ge=20)
    min_obs: int = Field(ge=10)


class QuantMarketConfig(_Strict):
    min_obs: int = Field(ge=10)


class QuantForwardConfig(_Strict):
    horizon_days: int = Field(ge=1)
    paths: int = Field(ge=100)
    block_length: int = Field(ge=1)
    outer_resamples: int = Field(ge=10)
    outer_paths: int = Field(ge=100)
    min_obs: int = Field(ge=20)
    drift_mode: Literal["zero", "historical"] = "zero"


class QuantMcDcfConfig(_Strict):
    simulations: int = Field(ge=100)
    growth_clamp: Tuple[float, float]
    growth_sd_floor: float = Field(ge=0)
    growth_clip: Tuple[float, float]
    discount_sd: float = Field(ge=0)
    terminal_range: Tuple[float, float]
    min_spread: float = Field(gt=0)


class QuantBaseRateConfig(_Strict):
    history_years: int = Field(ge=1)
    horizon_days: int = Field(ge=1)
    momentum_days: int = Field(ge=1)
    momentum_band: float = Field(gt=0)
    pe_band: float = Field(gt=0)
    min_cases: int = Field(ge=1)


class QuantCalibrationConfig(_Strict):
    path: str
    field: str
    bins: int = Field(ge=2)
    min_pairs: int = Field(ge=1)
    max_brier: float = Field(gt=0, le=1)


class QuantConfidenceConfig(_Strict):
    agreement_weight: float = Field(ge=0, le=1)
    contradiction_margin: float = Field(ge=0, lt=0.5)
    contradiction_penalty: float = Field(ge=0, le=1)


class QuantConfig(_Strict):
    seed: int
    returns: QuantReturnsConfig
    market: QuantMarketConfig
    forward: QuantForwardConfig
    mc_dcf: QuantMcDcfConfig
    base_rate: QuantBaseRateConfig
    calibration: QuantCalibrationConfig
    confidence: QuantConfidenceConfig


class Thresholds(_Strict):
    personas: Dict[Literal["zen", "balanced", "alpha"], PersonaThresholds]
    verification: VerificationConfig
    circuit_breaker: CircuitBreakerConfig
    judge: JudgeConfig
    integrity: IntegrityConfig
    capm: CapmConfig
    hitl: HitlConfig
    quant: QuantConfig

    @model_validator(mode="after")
    def _all_personas(self):
        missing = {"zen", "balanced", "alpha"} - set(self.personas)
        if missing:
            raise ValueError(f"personas missing: {sorted(missing)}")
        return self

    def persona(self, name: str) -> PersonaThresholds:
        key = "balanced" if name in ("sigma", "balanced") else name
        if key not in self.personas:
            raise ValueError(f"unknown persona {name!r}; expected one of {sorted(self.personas)}")
        return self.personas[key]


def _resolve(path: Optional[str]) -> Path:
    return Path(path or os.getenv("KAI_THRESHOLDS_PATH") or DEFAULT_PATH)


@lru_cache(maxsize=8)
def _load(path: str, mtime: float) -> Thresholds:
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    try:
        return Thresholds.model_validate(raw)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())
        raise ValueError(f"invalid thresholds file {path}: {problems}") from None


def load_thresholds(path: Optional[str] = None) -> Thresholds:
    """Read and validate the thresholds file. Cached per path and modification time, so a
    hand edit is picked up on the next call. Raises ValueError naming the bad key."""
    p = _resolve(path)
    if not p.exists():
        raise ValueError(f"thresholds file not found: {p}")
    return _load(str(p), p.stat().st_mtime)
