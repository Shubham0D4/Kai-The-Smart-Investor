"""
Quant layer (DESIGN.md §10): deterministic statistics built from already-fetched data.
No network calls, no LLM calls; randomness only through explicit seeds.
"""

from quant.models import QuantField, QuantReport, ReliabilityBin
from quant.report import LookAheadError, QuantInputs, build_quant_report, inputs_from_fetched

__all__ = ["QuantField", "QuantReport", "ReliabilityBin", "LookAheadError", "QuantInputs",
           "build_quant_report", "inputs_from_fetched"]
