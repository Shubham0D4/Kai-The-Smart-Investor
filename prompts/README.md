# Agent system prompts

Each agent's system prompt is a plain-text file in this folder. Edit a file and the change takes
effect on the next analysis: the files are read when an agent is created, and no restart or code
change is needed.

| File | Used by |
|---|---|
| `charlie.md` | Charlie, fundamentals (SEC filings) |
| `delta.md` | Delta, news and sentiment |
| `gamma.md` | Gamma, valuation and risk |
| `vega.md` | Vega, catalysts and options |
| `sigma.md` | Sigma, writes the verdict explanation (the verdict itself is computed in code) |

Rules shared by the four debating agents live in their own files and are pulled in with an
include line, so a rule edited once applies to every agent that includes it:

| File | What it says |
|---|---|
| `_lane_rule.md` | stay in your own domain; change a claim only on a factual correction; name the window of any change or return |
| `_evidence_rule.md` | use only the evidence supplied; every number must come from it |
| `_comms_protocol.md` | short fixed phrasing, reference formats, word limits |

Include syntax: a line containing only `{{include: _file.md}}` is replaced by that file's text.
Lines starting with `<!--` and ending with `-->` are comments and are removed before sending.

What NOT to change here, because code depends on it:
- the JSON reply format (it is enforced by a schema built in `agents/schema.py`);
- the evidence ids (`sec:...`, `mkt:...`) and reference formats (`charlie.C2`, `delta.K1`);
- the agents' names.
Prompts steer how agents argue; they cannot change the verdict, which is computed in `judge/`.
