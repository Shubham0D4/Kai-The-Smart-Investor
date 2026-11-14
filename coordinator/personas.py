"""
System instructions for Kai's agents, loaded from the prompts/ folder, and the RiskPersona enum.

The prompt text lives in prompts/<agent>.md so it can be edited without touching code (see
prompts/README.md). Files are read each time an agent is created, so an edit applies to the next
analysis. A line `{{include: _file.md}}` pulls in a shared rule; `<!-- ... -->` comments are
removed before the text is sent.

Persona is NOT part of any agent's instructions (FIXES.md C7, DESIGN.md §5). Agents analyse the
data the same way for every user; the persona only selects a row of decision thresholds in
config/thresholds.yaml, applied by the judge to the agents' combined output.
"""

import os
import re
from enum import Enum
from pathlib import Path

PROMPTS_DIR = Path(os.getenv("KAI_PROMPTS_DIR") or Path(__file__).resolve().parent.parent / "prompts")
AGENT_PROMPTS = ("charlie", "delta", "gamma", "vega", "sigma")

_INCLUDE = re.compile(r"^[ \t]*\{\{[ \t]*include:[ \t]*([\w.-]+)[ \t]*\}\}[ \t]*$", re.MULTILINE)
_COMMENT = re.compile(r"<!--.*?-->[ \t]*\n?", re.DOTALL)


class RiskPersona(Enum):
    """Risk persona: selects decision thresholds only (config/thresholds.yaml)."""
    ZEN = "zen"
    BALANCED = "balanced"
    ALPHA = "alpha"


def _read(name: str, seen: tuple = ()) -> str:
    path = PROMPTS_DIR / name
    if name in seen:
        raise ValueError(f"prompt include cycle: {' -> '.join(seen + (name,))}")
    if not path.is_file():
        raise ValueError(f"prompt file not found: {path}")
    text = _COMMENT.sub("", path.read_text(encoding="utf-8"))
    return _INCLUDE.sub(lambda m: _read(m.group(1), seen + (name,)).strip(), text)


def load_prompt(agent: str) -> str:
    """The system prompt for an agent, from prompts/<agent>.md with includes resolved."""
    if agent not in AGENT_PROMPTS:
        raise ValueError(f"unknown agent prompt {agent!r}; expected one of {AGENT_PROMPTS}")
    text = re.sub(r"\n{3,}", "\n\n", _read(f"{agent}.md")).strip()
    if not text:
        raise ValueError(f"prompt file prompts/{agent}.md is empty")
    return text


def get_charlie_system_instruction() -> str:
    return load_prompt("charlie")


def get_delta_system_instruction() -> str:
    return load_prompt("delta")


def get_gamma_system_instruction() -> str:
    return load_prompt("gamma")


def get_vega_system_instruction() -> str:
    return load_prompt("vega")


def get_sigma_system_instruction() -> str:
    return load_prompt("sigma")


if __name__ == "__main__":
    for a in AGENT_PROMPTS:
        print(f"===== {a}\n{load_prompt(a)}\n")
