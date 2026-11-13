"""
Data model for the quant layer (DESIGN.md §10.1).

Every number is a QuantField that carries its value, unit, interval (if any), the sample size or
number of simulations behind it, and whether it describes the past ("descriptive"), follows from
stated assumptions ("model") or is a probability about the future ("prediction"). A field that
cannot be computed is status="unavailable" with a reason and no value - never a default number.
"""

import math
from datetime import date
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, model_validator

Kind = Literal["descriptive", "model", "prediction"]
NKind = Literal["observations", "simulations", "cases", "pairs"]


class QuantField(BaseModel):
    id: str                                   # evidence id, e.g. "quant:returns.vol_annual"
    label: str
    status: Literal["ok", "unavailable"]
    value: Optional[float] = None
    unit: str
    interval: Optional[Tuple[float, float]] = None
    interval_level: Optional[float] = None
    n: Optional[int] = None
    n_kind: NKind
    kind: Kind
    method: str
    reason: Optional[str] = None

    @model_validator(mode="after")
    def _consistent(self):
        if self.status == "ok":
            if self.value is None or not math.isfinite(self.value):
                raise ValueError(f"{self.id}: ok field needs a finite value")
            if self.reason:
                raise ValueError(f"{self.id}: ok field cannot carry a reason")
        else:
            if self.value is not None or self.interval is not None:
                raise ValueError(f"{self.id}: unavailable field must not carry a number")
            if not self.reason:
                raise ValueError(f"{self.id}: unavailable field needs a reason")
        return self


def ok(id_: str, label: str, value: float, unit: str, n: int, n_kind: NKind, kind: Kind, method: str,
       interval: Optional[Tuple[float, float]] = None, interval_level: Optional[float] = None) -> QuantField:
    v = float(value)
    if not math.isfinite(v):
        return unavailable(id_, label, unit, n_kind, kind, method, f"computation gave a non-finite value ({v})", n)
    iv = (float(interval[0]), float(interval[1])) if interval is not None else None
    return QuantField(id=id_, label=label, status="ok", value=v, unit=unit, interval=iv,
                      interval_level=interval_level if iv else None, n=int(n), n_kind=n_kind,
                      kind=kind, method=method)


def unavailable(id_: str, label: str, unit: str, n_kind: NKind, kind: Kind, method: str, reason: str,
                n: Optional[int] = None) -> QuantField:
    return QuantField(id=id_, label=label, status="unavailable", unit=unit, n=n, n_kind=n_kind,
                      kind=kind, method=method, reason=reason)


class ReliabilityBin(BaseModel):
    low: float
    high: float
    n: int
    mean_stated: Optional[float]      # None for an empty bin
    observed_share: Optional[float]


class QuantReport(BaseModel):
    """The single output of build_quant_report."""
    as_of: date
    seed: int
    drift_mode: Literal["zero", "historical"]
    horizon_days: int
    input_dates: Dict[str, Optional[Tuple[date, date]]]     # first and last date of each input series
    fields: Dict[str, QuantField]                          # keyed by evidence id
    reliability: Optional[List[ReliabilityBin]] = None
    notes: List[str] = Field(default_factory=list)

    def get(self, field_id: str) -> Optional[QuantField]:
        return self.fields.get(field_id)

    def value(self, field_id: str) -> Optional[float]:
        f = self.fields.get(field_id)
        return f.value if f is not None and f.status == "ok" else None

    def evidence_records(self, include_unavailable: bool = False) -> List[Dict]:
        """Fields as evidence records (agents.schema.EvidenceRecord shape). Each ok field with an
        interval also yields `<id>.low` and `<id>.high`. Unavailable fields have value None, so a
        numeric claim citing them fails verification with `no_value`."""
        out = []
        for f in self.fields.values():
            n_text = f"n={f.n} {f.n_kind}" if f.n is not None else f.n_kind
            if f.status != "ok":
                if include_unavailable:
                    out.append({"id": f.id, "label": f"{f.label} (unavailable)", "value": None, "unit": f.unit,
                                "period": str(self.as_of), "source": f"quant layer: unavailable - {f.reason}",
                                "url": None})
                continue
            src = f"quant layer ({f.kind}): {f.method}; {n_text}"
            out.append({"id": f.id, "label": f.label, "value": f.value, "unit": f.unit,
                        "period": str(self.as_of), "source": src, "url": None})
            if f.interval:
                for side, v in zip(("low", "high"), f.interval):
                    out.append({"id": f"{f.id}.{side}", "label": f"{f.label}, {f.interval_level:.0%} interval {side}",
                                "value": v, "unit": f.unit, "period": str(self.as_of), "source": src, "url": None})
        return out

    def unavailable_summary(self) -> List[str]:
        return [f"{f.id}: {f.reason}" for f in self.fields.values() if f.status != "ok"]
