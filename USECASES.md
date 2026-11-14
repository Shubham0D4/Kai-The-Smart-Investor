# Kai — Use Cases

This document explains **who Kai is for, what problems it solves, and how to use it for each**, with the exact screens and fields to look at. For installation and configuration see [README.md](README.md); for design rationale see [DESIGN.md](DESIGN.md).

> Kai is a research tool, not investment advice. Every use case below is about *understanding and checking* an investment thesis, not about being told what to buy.

---

## Contents

1. [At a glance](#at-a-glance)
2. [What Kai is good at](#what-kai-is-good-at)
3. [Use cases](#use-cases)
   - [UC1 · Sanity-check a stock before you act](#uc1--sanity-check-a-stock-before-you-act)
   - [UC2 · Stress-test a thesis you already hold](#uc2--stress-test-a-thesis-you-already-hold)
   - [UC3 · Understand why the answer is "Hold" or "No Verdict"](#uc3--understand-why-the-answer-is-hold-or-no-verdict)
   - [UC4 · Compare risk appetites on the same evidence](#uc4--compare-risk-appetites-on-the-same-evidence)
   - [UC5 · Approve or reject high-conviction signals](#uc5--approve-or-reject-high-conviction-signals)
   - [UC6 · Keep an audit trail](#uc6--keep-an-audit-trail)
   - [UC7 · Speed up first-pass due diligence](#uc7--speed-up-first-pass-due-diligence)
   - [UC8 · Teach how evidence-grounded AI and risk statistics work](#uc8--teach-how-evidence-grounded-ai-and-risk-statistics-work)
   - [UC9 · Measure whether the forecasts deserve trust](#uc9--measure-whether-the-forecasts-deserve-trust)
   - [UC10 · Extend or evaluate the system (developers and researchers)](#uc10--extend-or-evaluate-the-system-developers-and-researchers)
   - [UC11 · Present Kai to stakeholders](#uc11--present-kai-to-stakeholders)
4. [Worked example: AAPL](#worked-example-aapl)
5. [Reading the output: a glossary](#reading-the-output-a-glossary)
6. [When Kai is the wrong tool](#when-kai-is-the-wrong-tool)
7. [FAQ](#faq)

---

## At a glance

| # | Use case | Typical user | Look at | You walk away with |
|---|---|---|---|---|
| UC1 | Sanity-check a stock | Individual investor | Verdict, gates, quant report | A grounded second opinion and the reasons behind it |
| UC2 | Stress-test a thesis | Investor with a position | Contradiction flags, fair-value range | Where the data disagrees with you |
| UC3 | Understand uncertainty | Anyone | Banner, committee grid, warnings | Why Kai declined to commit, and what was missing |
| UC4 | Compare risk appetites | Advisor, household decision-makers | Persona table | How strict thresholds change the call |
| UC5 | Human approval | Reviewer, supervisor | Approval dialog, packet | A deliberate, recorded decision |
| UC6 | Audit trail | Compliance, team lead | Signed packet, debate log, token trace | A reproducible record |
| UC7 | First-pass diligence | Analyst | Claims with evidence, source popovers | A fact-checked starting point |
| UC8 | Teaching | Instructor, student | Round 2 and 3, verification marks | A transparent example of AI reasoning under checks |
| UC9 | Forecast quality | Quant, researcher | Calibration script | Reliability table and Brier score |
| UC10 | Extend / evaluate | Developer, researcher | Live tests, prompts, config | A system you can change and re-measure on real data |
| UC11 | Present | Founder, product owner | The whole dashboard | A demo that survives hard questions |

## What Kai is good at

- **Making the model prove its numbers.** Each figure an agent states is re-computed from fetched data. A wrong number is shown as a failure, with the value the data actually gives.
- **Showing disagreement instead of hiding it.** Agents challenge each other's specific claims, and you can read what changed their minds.
- **Separating facts from forecasts.** The quant report labels every statistic as describing the past, following from stated assumptions, or being a prediction, and shows the sample size.
- **Refusing to guess.** Missing data, failed fact-checks or an exhausted budget produce **No Verdict** with the reason.
- **Being reproducible.** A fixed seed, a persisted transcript and a signed packet make a result inspectable after the fact.

---

## Use cases

### UC1 · Sanity-check a stock before you act

**Who:** an individual investor considering a position, or one reviewing a holding.

**Goal:** get a second opinion that is grounded in filings and market data, and that shows its work.

**Steps**
1. Open the dashboard, type a ticker, pick the thresholds that match your appetite (Balanced is a sensible default), and press **Run analysis**.
2. Watch the committee grid: Charlie (SEC fundamentals), Delta (news), Gamma (valuation and risk) and Vega (catalysts and options) work in turn, each only in its own domain; Sigma judges last. The verdict card shows the market snapshot time every agent worked from.
3. Read the **verdict card**: the recommendation and its confidence, the decision gates, the verified supporting points, and **What to watch**: what would turn the verdict into Buy or Sell, price levels, the next earnings date with the options-implied move, and past swings.
4. Open **Quant report** and compare the fair-value range with the last price.
5. Open **What each agent said** and click an evidence chip to see the source behind a number.

**How to read it**
- *Hold* is a normal, informative result: the debate did not clear the Buy or Sell bar. Its confidence says how firmly: a high number means the debate sits well inside the Hold zone (or a gate missed by a wide margin, for example "margin of safety −1.70 < +0.25"); a low number means it is close to a trigger, so the "Would become Buy/Sell if" items matter.
- *Faithfulness below 100%* means some stated numbers did not match the data. Expand that agent to see which claim failed and what the data says instead.

**Caution:** a verdict summarises evidence at one moment. It does not account for events that have not happened yet.

---

### UC2 · Stress-test a thesis you already hold

**Who:** an investor who is already bullish (or bearish) and wants to know where the numbers disagree.

**Goal:** find the strongest evidence *against* your view.

**What Kai shows**
- **Contradiction flags.** If an agent is bullish but the quant report says the probability of the price being higher, or of fair value exceeding price, is low (below 45%), the verdict card lists the contradiction by name and **reduces the confidence**. A bearish agent contradicting a high probability (above 55%) is flagged the same way.
- **Round-2 challenges.** Each agent disputes, qualifies or supports specific claims of the others, citing evidence from its own data, so you see which numbers actually clash.
- **The fair-value distribution.** Not one DCF number but a p10–p90 range from Monte Carlo simulation, set against the last price.

**Example reading:** "Charlie, Delta and Gamma are bullish, but 'Share of simulations with fair value above price' is 6%. Confidence was reduced from 80% to 31%." That is a prompt to inspect the DCF inputs, not a reason to sell.

---

### UC3 · Understand why the answer is "Hold" or "No Verdict"

**Who:** anyone surprised that Kai did not commit.

**Goal:** know whether the uncertainty comes from the market, the data or the system.

| What you see | What it means | What to do |
|---|---|---|
| **Hold** (with a confidence in the Hold) | The debate score or a gate did not clear the Buy/Sell bar | Read the gates and the "Would become Buy/Sell if" suggestions; try another threshold set (UC4) |
| **Verdict based on fewer agents** | An agent failed or was dropped; the verdict uses the rest | Expand the missing agent for its error or failed claims |
| **Circuit breaker: too few agents passed the fact-check** | At least two agents stated numbers that did not match the data, so the debate stopped after round 1 | Read the failed claims; the data or the model output was unreliable |
| **Not enough data to start a debate** | SEC, market or news data was unavailable for too many agents | Check the ticker and the network, then retry |
| **Stopped: token budget used up** | The 50,000-token budget was reached | Retry; consider a more concise model |
| **Failed** | An unexpected error (for example both LLM providers refused the call) | The banner shows the provider messages, not a guess |

In every "no verdict" case Kai states that **no recommendation was produced and none is guessed**.

---

### UC4 · Compare risk appetites on the same evidence

**Who:** an advisor discussing options with a client, or a household where members have different tolerance for risk.

**Goal:** show how much of the answer comes from the evidence and how much from risk appetite.

The three personas **only change decision thresholds**; the agents analyse identically and all three verdicts are computed from the same debate. The verdict card includes a table of all three:

| Persona | Needs for a Buy | Typical effect |
|---|---|---|
| **Zen** | Debate score ≥ 0.50, margin of safety ≥ 25%, volatility ≤ 35% | Most Holds; rarely buys expensive or volatile stocks |
| **Balanced** | Score ≥ 0.35, margin ≥ 15%, volatility ≤ 50% | Middle ground |
| **Alpha** | Score ≥ 0.25, no margin or volatility limit | Willing to buy on momentum and sentiment; can buy without a DCF |

The numbers live in `config/thresholds.yaml` and can be tuned to a house view without touching code.

---

### UC5 · Approve or reject high-conviction signals

**Who:** a reviewer who must consciously sign off before anything is acted on.

**Goal:** make strong signals require a deliberate human decision.

**Flow**
1. A Buy or Sell with confidence **≥ 60** pauses the session as *waiting for approval* and opens the approval dialog: ticker, recommendation, confidence, key points and the expiry time.
2. **Approve** or **Reject**. Both are recorded against the session. A challenge that has expired, or the wrong challenge id, is refused with a clear message.
3. A Buy or Sell **below 60** is not passed silently: it is shown as an explicit low-confidence warning.
4. The pending approval **survives a server restart**; it is stored with the session.

Approval does not place a trade. It records a decision; Kai has no brokerage integration.

---

### UC6 · Keep an audit trail

**Who:** a team lead, a compliance function, or anyone who must explain a decision later.

**Goal:** reconstruct exactly what was analysed and why.

| Artefact | What it contains | Where |
|---|---|---|
| **Signed alpha packet** | Verdict, confidence, quality scores, risks, gates, the full quant report; Ed25519 signature | `alpha_packets/` and the API |
| **Debate log** | Every agent output, critique, change of mind, verification result and system step, as recorded | `debate_logs/` |
| **Token trace** | Every model call with its tokens, including rejected replies | "LLM calls and token budget" in the UI |
| **Quant report seed** | Same inputs and seed reproduce the same statistics | recorded in the report |
| **History** | All sessions, including failed ones | "Recent analyses" and `/api/v1/history` |

A packet's signature shows it was not altered after signing by this key. It does not show the analysis was right.

---

### UC7 · Speed up first-pass due diligence

**Who:** an analyst starting coverage of a name.

**Goal:** get a structured, fact-checked starting point in minutes, then spend time on judgement.

**How to use it**
- Treat each agent's claims as a **checklist of verifiable statements**: revenue growth, margins, cash conversion, leverage, valuation multiples, volatility.
- Click any evidence chip (for example `sec:operating_cash_flow:FY2026`) to see the figure, period and the SEC filing it came from, then open the source.
- Use the **fact-check marks** to know which statements were machine-verified and which are interpretation.
- Use the **round-3 changes** to see which arguments survived scrutiny.

**What it does not replace:** reading the filing's management discussion, understanding the business model, competitive position, governance, or anything qualitative the structured data does not hold.

---

### UC8 · Teach how evidence-grounded AI and risk statistics work

**Who:** an instructor or student in finance, data science or AI.

**Why it works for teaching:** every step is visible and every number traceable.

- **Evidence-grounded generation:** show a claim, its evidence ids, and the re-computation that verified or rejected it.
- **Adversarial review:** round 2 shows agents challenging specific claims; round 3 shows changes of mind with reasons.
- **Verification as a separate process:** a wrong number is caught by code, not by another model.
- **Honest statistics:** the quant report labels what is descriptive, modelled or predicted, gives sample sizes, and shows "unavailable" instead of a default.
- **Experiments:** edit `config/thresholds.yaml`, re-run, and see the verdict change; switch `drift_mode` to see how much a forecast depends on assuming the past trend continues.

---

### UC9 · Measure whether the forecasts deserve trust

**Who:** a quant or researcher who wants evidence on calibration, not claims.

**Goal:** compare Kai's stated probabilities with what happened.

```bash
python scripts/calibration_backtest.py --tickers AAPL MSFT --start 2018-01-01 --end 2025-06-30
```

For each ticker and past date, the script builds the quant report **using only data available at that date** (and asserts no input row is dated later), records the stated probabilities that the price will be higher after 63 trading days, compares them with the outcome, and writes `pairs.csv` plus a reliability table and **Brier score**. Once at least 20 pairs exist, the report's calibration field is populated, and a Brier score worse than a coin flip (0.25) makes Kai **ignore its own forward probability** when adjusting confidence.

This script is live (network) and is not part of the automated tests.

---

### UC10 · Extend or evaluate the system (developers and researchers)

**Who:** engineers adding data sources or models, or researchers evaluating agent systems.

- **Tested on real data with real models.** The suite runs complete live debates and re-derives every property independently (fact-check, score, confidence, snapshot consistency, lane rule, suggestion numbers). Results with all numbers: [TESTRESULTS.md](TESTRESULTS.md).
- **Tune how agents argue.** Each agent's system prompt is a file in `prompts/`; shared rules (stay in your lane, cite evidence, comms protocol) are included from one place. Rerun the live suite to measure the effect.
- **Swap providers or models.** Edit the per-agent model chains in `config/llm.yaml` (list what your keys can use with `python -m coordinator.llm_client --list`); the trace panel shows which model answered each step and where its tokens went.
- **Tune behaviour in one file.** Thresholds, fact-check tolerance, circuit-breaker floors, CAPM constants and simulation sizes are in `config/thresholds.yaml`.
- **Strict contracts.** Agent output, critiques, final positions, the quant report and Sigma's verdict are pydantic models; the frontend tests render against real API payloads, and a backend test fails if the API shape drifts from them.

---

### UC11 · Present Kai to stakeholders

**Who:** a founder, product owner or student presenting the project.

**Suggested flow (about five minutes)**
1. **The idea (30 s).** "Most AI stock tools give a paragraph and a rating. Kai makes the model prove every number, then computes the verdict with fixed rules."
2. **Run a live analysis.** Show the committee working, then the verdict card.
3. **Open an evidence chip.** Click a figure and show its SEC source. This is the strongest moment of the demo.
4. **Show disagreement.** Open round 2 and round 3 for one agent: a challenge, a change of mind, the reason.
5. **Show the guardrails.** Show a failed fact-check or a circuit-breaker banner: "when the data does not support a claim, Kai says so."
6. **Show the quant report.** Point at the fair-value range, the sample sizes and the "describes the past / model / prediction" labels.
7. **Close with limits.** Name them before you are asked (see [README → Limitations](README.md#limitations)).

**Prepare for these questions**
- *"Is the recommendation any good?"* Kai does not claim accuracy. Run the calibration backtest (UC9) before making any performance statement.
- *"What stops the model making things up?"* Numbers and quotes are re-checked in code; qualitative risk statements are **not** checked.
- *"Can it trade?"* No. Approval records a decision only.
- *"Is it secure?"* It is a local single-user app with no authentication. It must not be exposed on a network as is.
- Run one real analysis before presenting; the live results in TESTRESULTS.md show what real output looks like.

---

## Worked example: AAPL

From the live test run on 2026-11-14 (real models, real data; full record in [TESTRESULTS.md](TESTRESULTS.md)). It illustrates how to read the output; it is not a recommendation.

**One snapshot.** Every agent worked from price **$356.27**, fetched 2026-11-14 05:07 UTC (last close 2026-11-13). P/E **39.67** = price ÷ trailing-twelve-month diluted EPS **$8.98** (2025-09-28 to 2026-09-26). Returns carry their windows: **+4.3%** over 1 month (2026-10-15 to 11-13), **+30.7%** over 1 year.

**The committee (Zen thresholds).** Charlie (fundamentals) bullish 80 and Delta (news) bullish 85; Vega (catalysts) bearish 72.5, citing 23 downward against 7 upward EPS revisions in 30 days. Gamma (valuation) opened bearish 85 but ran out of its token share before round 3, so its round-1 view stands. Charlie's four claims were all re-computed and verified, for example "Revenue FY2026 equals $478.834 billion" against `sec:revenue:FY2026`.

**Verdict: Hold, 74% confidence in Hold.** The weighted debate score is **+0.01**, nowhere near Zen's buy trigger (+0.50) or sell trigger (−0.20), so the Hold is firm (conviction 0.98). Even a higher score would not buy: the margin of safety against the point DCF is **−1.73**, against Zen's required +0.25.

**What to watch (computed).**
- Would become Buy if the score reaches +0.50 **and** the price is at or below **$97.87**, a 25% margin of safety on the DCF value of $130.49.
- The Monte Carlo fair-value range is **$41–$325** (median $127); the price is above the whole range, and only 6% of simulations put fair value above it.
- The analyst mean target is $344.62, **−3.3%** from the snapshot price.
- Earnings are in 77 days; options imply about a **±10.3%** move by the first expiry after them.
- Over the past year, 1 day in 20 lost 2.0% or more; the worst fall was 13.8%; volatility was 25%.
- The quant report disagrees with Charlie and Delta's bullish stances, so check that before acting.

**Unavailable, on purpose.** Base rates (too few comparable cases) and calibration (no recorded forecasts yet) show the reason instead of a number.

---

## Reading the output: a glossary

| Term | Meaning |
|---|---|
| **Verdict** | Buy, Hold, Sell or No Verdict, computed by rules from the debate and the persona's thresholds |
| **Confidence** | Buy/Sell: `100 × abs(score) × integrity × coverage × agreement × contradiction factor` (lowered, never raised, by the quant report). Hold: `100 × hold conviction × integrity × coverage` |
| **Hold conviction** | How firmly a Hold is a Hold: distance from the score to the nearer Buy/Sell trigger, or how far a blocking gate missed; 0 = on the edge, 1 = firm |
| **Suggestions / What to watch** | Conditions, levels, events and risks computed from the evidence (`judge/playbook.py`); research, not advice |
| **Debate score** | Weighted average (judge's domain weights) of stance × confidence × faithfulness over agents that passed the fact-check; −1 (bearish) to +1 (bullish) |
| **Faithfulness** | Share of an agent's checkable claims that match the fetched data |
| **Integrity** | How well critiques were answered and failed claims corrected |
| **Circuit breaker** | After round 1, agents under 60% faithfulness are dropped; with fewer than two left the debate ends with No Verdict |
| **Evidence id** | A reference to a fetched or computed record, such as `sec:revenue:FY2026` or `quant:market.beta` |
| **Margin of safety** | (DCF value − price) ÷ DCF value; negative means the price is above the DCF value |
| **VaR (95%, 1-day)** | The loss exceeded on about 5% of days, as a fraction of the position |
| **Beta** | Sensitivity of the stock's daily returns to the S&P 500's |
| **Brier score** | Mean squared error of stated probabilities; 0 is perfect, 0.25 is a coin flip |
| **Descriptive / model / prediction** | Describes the past / follows from stated assumptions / a probability about the future |
| **Alpha packet** | The signed record of a verdict, its scores, risks and quant report |
| **Persona** | A row of decision thresholds (Zen, Balanced, Alpha); never shown to the agents |

---

## When Kai is the wrong tool

- **Trading or execution.** Kai does not place orders, size positions or manage risk limits.
- **Intraday or high-frequency decisions.** An analysis takes minutes and uses daily data.
- **Non-US or non-equity assets.** Crypto, funds, bonds and foreign filers are outside what it was built and tested for.
- **Replacing judgement.** It checks numbers; it does not understand strategy, management quality or an upcoming catalyst.
- **A shared or public service as is.** It has no authentication and is designed for one user on one machine.
- **Guaranteeing accuracy.** Until the calibration backtest has been run and reviewed, Kai makes no claim about how often its probabilities are right.

## FAQ

**Does the AI decide the verdict?** No. A language model argues and writes the explanation. The verdict, scores and confidence are computed in code, and a test confirms a model reply saying "strong sell" cannot change a computed Buy.

**Why does a Hold have a confidence?** It tells you how firmly the debate sits inside the Hold zone. A Hold at 90% is a settled "not now"; a Hold at 15% is close to flipping, so watch the triggers. Only No Verdict carries no confidence.

**Why was my confidence lowered?** The quant report disagreed with the agents (its probabilities point against the verdict) or contradicted individual agents' stances. The verdict card shows both factors.

**An agent "failed". Is that a bug?** Usually it is the system doing its job: the model returned invalid output twice, a data source was down, or its numbers did not match the data. The banner and the agent's tab say which.

**Can I change how strict Buy and Sell are?** Yes: edit `config/thresholds.yaml`. No code change is needed.

**Does Kai send my data anywhere?** Prompts and evidence go to the LLM provider you configured, and data requests go to SEC EDGAR, Yahoo Finance and news feeds. Nothing else leaves your machine.

**Why is the base rate usually unavailable?** One stock's own history rarely contains 30 comparable situations. Kai shows "unavailable" rather than a number built on too few cases.

**Can I trust a verified claim?** It means the number matches the fetched data, not that the data is complete or that the conclusion drawn from it is sound.
