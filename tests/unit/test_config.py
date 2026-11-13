"""
PHASE4_PLAN.md §9: config/thresholds.yaml loads, validates, can be edited by hand and moved.
"""

import os

import pytest
import yaml

from agents.schema import AgentOutput
from coordinator.config import DEFAULT_PATH, load_thresholds
from judge.scoring import decide_verdict


def _write(tmp_path, mutate):
    data = yaml.safe_load(DEFAULT_PATH.read_text())
    mutate(data)
    path = tmp_path / "thresholds.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def _bullish_outputs():
    claim = {"id": "C1", "kind": "numeric", "text": "t", "value": 1.0, "unit": "USD", "op": "equals", "evidence": ["e"]}
    return {a: AgentOutput.model_validate({"agent": a, "round": 1, "stance": "bullish", "confidence": 80,
                                           "claims": [claim]}) for a in ("charlie", "delta", "gamma")}


def test_default_file_loads():
    cfg = load_thresholds()
    assert set(cfg.personas) == {"zen", "balanced", "alpha"}
    assert cfg.persona("zen").buy_score == 0.50
    assert cfg.persona("sigma") == cfg.persona("balanced")       # old name still accepted
    assert cfg.hitl.approval_confidence == 60


def test_missing_key_names_the_key(tmp_path):
    path = _write(tmp_path, lambda d: d["personas"]["zen"].pop("buy_score"))
    with pytest.raises(ValueError, match=r"personas\.zen\.buy_score"):
        load_thresholds(str(path))


def test_unknown_key_rejected(tmp_path):
    path = _write(tmp_path, lambda d: d["capm"].update(beta=1.0))
    with pytest.raises(ValueError, match=r"capm\.beta"):
        load_thresholds(str(path))


def test_editing_a_threshold_changes_the_verdict(tmp_path):
    outs = _bullish_outputs()
    faith = {a: 1.0 for a in outs}
    default = load_thresholds()
    assert decide_verdict(outs, faith, 1.0, 0.10, 0.25, default.persona("zen"), default).recommendation == "Hold"

    path = _write(tmp_path, lambda d: d["personas"]["zen"].update(min_margin_of_safety=0.05))
    edited = load_thresholds(str(path))
    assert decide_verdict(outs, faith, 1.0, 0.10, 0.25, edited.persona("zen"), edited).recommendation == "Buy"


def test_env_var_is_honoured_and_hand_edits_are_picked_up(tmp_path, monkeypatch):
    path = _write(tmp_path, lambda d: d["hitl"].update(approval_confidence=75))
    monkeypatch.setenv("KAI_THRESHOLDS_PATH", str(path))
    assert load_thresholds().hitl.approval_confidence == 75

    data = yaml.safe_load(path.read_text())
    data["hitl"]["approval_confidence"] = 65
    path.write_text(yaml.safe_dump(data))
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
    assert load_thresholds().hitl.approval_confidence == 65
