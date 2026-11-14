# Kai: Test results

Live run from **2026-11-14T10:33:00+0530** to **2026-11-14T11:04:57+0530**. Every test used **real LLM calls** (Groq and Google Gemini, models as in `config/llm.yaml`) and **real data** (SEC EDGAR, Yahoo Finance, public news feeds). There is no fake model and no recorded data in the suite.

**51 passed, 2 failed, 0 skipped** out of 53 tests.

- Debates, data and gateway tests: one run 2026-11-14T10:33:00+0530 to 2026-11-14T11:02:38+0530 (the model checks of that run were interrupted when a Gemini request hung with no timeout).
- Model checks: re-run 2026-11-14T11:04:46+0530 to 2026-11-14T11:04:57+0530 after adding request timeouts.

Reproduce: `python -m pytest` (needs `GROQ_API_KEY` and `GOOGLE_API_KEY`; about 15-25 minutes and roughly 200k tokens), then `python scripts/write_test_results.py`. Results differ between runs because markets, news and model outputs change.

## 1. Live debates

| Ticker | Persona | Verdict | Confidence | Debate score | Faithfulness | Integrity | Tokens (limit) | Time |
|---|---|---|---|---|---|---|---|---|
| NVDA | balanced | **Hold** | 9.6% in Hold | +0.305 | 100.0 | 75.0 | 62,292 (70,000) | 250s |
| AAPL | zen | **Hold** | 73.5% in Hold | +0.010 | 100.0 | 75.0 | 61,576 (70,000) | 615s |
| TSLA | alpha | **Hold** | 46.7% in Hold | +0.429 | 93.3 | 66.7 | 42,903 (70,000) | 817s |

### NVDA (balanced thresholds)

Market snapshot: price **$162.85** fetched 2026-11-14T05:03:14Z (last close 2026-11-13); TTM EPS 7.91 (2025-07-26..2026-07-26).

| Agent | State | Round 1 | Round 3 | Fact-check R1 | Fact-check final | Claims | Critiques |
|---|---|---|---|---|---|---|---|
| Charlie | done | bullish 85 | bullish 85 | 100% | 100% | 4 | 4 |
| Delta | done | bearish 85 | bearish 80 | 100% | 100% | 5 | 3 |
| Gamma | failed (token budget: this agent's token share is used up (~0 tokens) | bullish 70 | – | 100% | – | 5 | 3 |
| Vega | done | neutral 65 | neutral 65 | 100% | 100% | 7 | 3 |

Token use per step (prompt / visible output / hidden reasoning; model that answered):

| Agent | Round | Prompt | Output | Reasoning | Total | Model | Result |
|---|---|---|---|---|---|---|---|
| charlie | 1 | 3,186 | 300 | 467 | 3,953 | groq:openai/gpt-oss-120b | accepted |
| delta | 1 | 2,487 | 550 | 0 | 3,037 | gemini:gemini-3.5-flash-lite | accepted |
| gamma | 1 | 4,282 | 478 | 258 | 5,018 | groq:openai/gpt-oss-120b | accepted |
| vega | 1 | 1,959 | 671 | 0 | 2,630 | gemini:gemini-3.5-flash | accepted |
| charlie | 2 | 7,013 | 615 | 396 | 8,024 | groq:openai/gpt-oss-120b → groq:openai/gpt-oss-120b | repaired |
| delta | 2 | 4,645 | 260 | 0 | 4,905 | gemini:gemini-3.5-flash-lite | accepted |
| gamma | 2 | 7,756 | 285 | 183 | 8,224 | groq:openai/gpt-oss-120b → groq:openai/gpt-oss-120b | repaired |
| vega | 2 | 4,231 | 218 | 1,191 | 5,640 | gemini:gemini-3.5-flash | accepted |
| charlie | 3 | 6,237 | 458 | 119 | 6,814 | groq:openai/gpt-oss-120b | accepted |
| delta | 3 | 6,748 | 1,555 | 0 | 8,303 | gemini:gemini-3.5-flash-lite → gemini:gemini-3.5-flash-lite | repaired |
| gamma | 3 | 0 | 0 | 0 | 0 | – | budget |
| vega | 3 | 4,823 | 921 | 0 | 5,744 | gemini:gemini-3.5-flash | accepted |

Budget shares: charlie 18,791 / 14,700, delta 16,245 / 11,900, gamma 13,242 / 16,100, vega 14,014 / 13,300; reserve left 4,850 of 14,000.

Verdict under each persona's thresholds (same debate): zen Hold (29.2), balanced Hold (9.6), alpha Buy (22.9).

Gates: FAILED S +0.30 < buy threshold +0.35; FAILED no DCF available: Buy requires a margin of safety; PASSED volatility 0.39 <= 0.50; FAILED S +0.30 > sell threshold -0.30.

Suggestions (computed playbook):

- **No action signal: hold or wait** (action): Neither the Buy nor the Sell rule is met under Balanced thresholds (debate score +0.30). Confidence in Hold 10%: the debate is close to a Buy or Sell trigger, so watch the triggers below.
- **Would become Buy if** (trigger): debate score reaches +0.35 (now +0.30); and a DCF value can be computed (none was: Buy is not possible under these thresholds).
- **Would become Sell if** (trigger): the debate score falls to -0.30 or lower (now +0.30).
- **Analyst consensus target** (level): Mean target $283.31, +74.0% from the snapshot price.
- **Earnings ahead** (event): Next earnings report in 5 days. Options imply a move of about 8.6% either way by the first expiry after it; a decision made before the report carries that event risk.
- **Estimate revisions lean upward** (event): 2 upward and 0 downward EPS revisions for this fiscal year in the last 30 days.
- **Size for the swings** (risk): Over the past year, 1 trading day in 20 lost 4.19% or more, the worst peak-to-trough fall was 31.9%, and annualised volatility was 39%. Past swings, not a forecast.
- **Other risk settings disagree** (context): Same debate, different thresholds: Alpha gives Buy.

Sigma's explanation (model-written, verbatim):

> The committee’s verdict is Hold with a confidence of 10 %. The debate score sits at +0.30, which fails the buy gate that requires at least +0.35 and also fails the buy gate that needs a DCF‑based margin of safety. The volatility gate passes (0.39 ≤ 0.50), but the score is above the sell threshold of –0.30, so the sell gate fails as well. Analysts’ mean price target of $283.31 suggests roughly 74 % upside, yet sentiment has weakened with ten downgrades and 19 price‑target cuts in the past month. Options show elevated at‑the‑money volatility ahead of earnings in five days and imply an earnings move of about 8.6 %. Risks highlighted include a possible slowdown in AI demand, higher R&D spending hurting cash flow, tighter macro‑finance, a sudden CEO resignation and supplier‑concentration concerns. Investors should watch the upcoming earnings report, the evolution of analyst sentiment and price‑target revisions, and any change in the debate score toward the +0.35 buy trigger or a drop below –0.30 that would flip the recommendation.

### AAPL (zen thresholds)

Market snapshot: price **$356.27** fetched 2026-11-14T05:07:24Z (last close 2026-11-13); TTM EPS 8.98 (2025-09-28..2026-09-26).

| Agent | State | Round 1 | Round 3 | Fact-check R1 | Fact-check final | Claims | Critiques |
|---|---|---|---|---|---|---|---|
| Charlie | done | bullish 80 | bullish 80 | 100% | 100% | 4 | 3 |
| Delta | done | bullish 85 | bullish 85 | 100% | 100% | 4 | 3 |
| Gamma | failed (token budget: this agent's token share is used up (~0 tokens) | bearish 85 | – | 100% | – | 6 | 3 |
| Vega | done | bearish 72 | bearish 72 | 100% | 100% | 5 | 4 |

Token use per step (prompt / visible output / hidden reasoning; model that answered):

| Agent | Round | Prompt | Output | Reasoning | Total | Model | Result |
|---|---|---|---|---|---|---|---|
| charlie | 1 | 5,120 | 578 | 412 | 6,110 | groq:openai/gpt-oss-120b → groq:openai/gpt-oss-120b | repaired |
| delta | 1 | 2,825 | 405 | 0 | 3,230 | gemini:gemini-3.5-flash-lite | accepted |
| gamma | 1 | 4,782 | 543 | 320 | 5,645 | groq:openai/gpt-oss-120b | accepted |
| vega | 1 | 1,961 | 527 | 0 | 2,488 | gemini:gemini-3.5-flash | accepted |
| charlie | 2 | 4,852 | 311 | 491 | 5,654 | groq:openai/gpt-oss-120b | accepted |
| delta | 2 | 4,795 | 141 | 0 | 4,936 | gemini:gemini-3.5-flash-lite | accepted |
| gamma | 2 | 8,212 | 383 | 580 | 9,175 | gemini:gemini-2.5-flash → groq:openai/gpt-oss-120b | repaired |
| vega | 2 | 3,948 | 373 | 0 | 4,321 | gemini:gemini-3.5-flash | accepted |
| charlie | 3 | 5,959 | 636 | 105 | 6,700 | groq:openai/gpt-oss-120b | accepted |
| delta | 3 | 6,729 | 1,168 | 0 | 7,897 | gemini:gemini-3.5-flash-lite → gemini:gemini-3.5-flash-lite | repaired |
| gamma | 3 | 0 | 0 | 0 | 0 | – | budget |
| vega | 3 | 4,623 | 797 | 0 | 5,420 | gemini:gemini-3.5-flash | accepted |

Budget shares: charlie 18,464 / 14,700, delta 16,063 / 11,900, gamma 14,820 / 16,100, vega 12,229 / 13,300; reserve left 6,073 of 14,000.

Verdict under each persona's thresholds (same debate): zen Hold (73.5), balanced Hold (72.9), alpha Hold (72.0).

Gates: FAILED S +0.01 < buy threshold +0.50; FAILED margin of safety -1.73 < +0.25; PASSED volatility 0.25 <= 0.35; FAILED S +0.01 > sell threshold -0.20.

Suggestions (computed playbook):

- **No action signal: hold or wait** (action): Neither the Buy nor the Sell rule is met under Zen thresholds (debate score +0.01). Confidence in Hold 74%: the debate sits well inside the Hold zone.
- **Would become Buy if** (trigger): debate score reaches +0.50 (now +0.01); and price is at or below $97.87 (a 25% margin of safety on the DCF value $130.49; price now $356.27).
- **Would become Sell if** (trigger): the debate score falls to -0.20 or lower (now +0.01).
- **Fair-value range (Monte Carlo DCF)** (level): $41.12 to $324.63, median $126.53; the price $356.27 is above the whole range (6% of simulations put fair value above the price).
- **Analyst consensus target** (level): Mean target $344.62, -3.3% from the snapshot price.
- **Earnings ahead** (event): Next earnings report in 77 days. Options imply a move of about 10.3% either way by the first expiry after it; a decision made before the report carries that event risk.
- **Estimate revisions lean downward** (event): 7 upward and 23 downward EPS revisions for this fiscal year in the last 30 days.
- **Size for the swings** (risk): Over the past year, 1 trading day in 20 lost 2.00% or more, the worst peak-to-trough fall was 13.8%, and annualised volatility was 25%. Past swings, not a forecast.
- **Quant report disagrees** (context): Charlie, Delta lean against the quant report's probabilities; check that disagreement before acting.

Sigma's explanation (model-written, verbatim):

> The committee concludes a Hold on Apple with 74 percent confidence. The debate score of +0.01 fails the buy gate (+0.01 < +0.50) and also fails the sell gate (+0.01 > ‑0.20), while the margin‑of‑safety test fails (‑1.73 < +0.25) and volatility passes (0.25 ≤ 0.35). Fundamentals are strong: revenue is projected at $478.8 billion for FY2026, operating margin at 33 percent and long‑term debt at $78.1 billion, showing improving margins and falling leverage. Recent price action has been positive, with a 4.25 percent return over the last month, but the current price of $356.27 sits well above the Monte Carlo DCF fair‑value range of $41.12‑$324.63 and the analyst mean target of $344.62, indicating limited upside. Risks include a possible slowdown in revenue growth, supply‑chain strains, regulatory cost pressures, broader macro slowdown, and sector volatility from semiconductor and leadership issues. Investors should watch the next earnings release in roughly 77 days, monitor any shift in the debate score toward the buy (+0.50) or sell (‑0.20) thresholds, and track revisions to EPS estimates, which have been predominantly downward (23 cuts vs 7 lifts). Also keep an eye on the disagreement between the agents’ bullish views and the quant model’s low probability (6 %) that fair value exceeds the current price.

### TSLA (alpha thresholds)

Market snapshot: price **$390.22** fetched 2026-11-14T05:17:39Z (last close 2026-11-13); TTM EPS 0.91 (2025-09-30..2026-09-30).

| Agent | State | Round 1 | Round 3 | Fact-check R1 | Fact-check final | Claims | Critiques |
|---|---|---|---|---|---|---|---|
| Charlie | failed (LLM call failed: all models failed for charlie: groq:openai/) | neutral 78 | – | 83% | – | 7 | 4 |
| Delta | done | bullish 75 | bullish 75 | 100% | 100% | 3 | 2 |
| Gamma | dropped (dropped: faithfulness 0.33 < 0.60 (1/2 checkable claims verified)) | bearish 85 | – | 33% | – | 3 | 0 |
| Vega | done | bullish 75 | bullish 75 | 100% | 100% | 8 | 2 |

Token use per step (prompt / visible output / hidden reasoning; model that answered):

| Agent | Round | Prompt | Output | Reasoning | Total | Model | Result |
|---|---|---|---|---|---|---|---|
| charlie | 1 | 3,029 | 669 | 406 | 4,104 | groq:openai/gpt-oss-120b | accepted |
| delta | 1 | 2,542 | 377 | 0 | 2,919 | gemini:gemini-3.5-flash-lite | accepted |
| gamma | 1 | 4,617 | 355 | 307 | 5,279 | groq:openai/gpt-oss-120b | accepted |
| vega | 1 | 1,966 | 802 | 0 | 2,768 | gemini:gemini-3.5-flash | accepted |
| charlie | 2 | 4,509 | 231 | 162 | 4,902 | groq:openai/gpt-oss-120b | accepted |
| delta | 2 | 4,336 | 100 | 352 | 4,788 | groq:openai/gpt-oss-20b | accepted |
| vega | 2 | 3,783 | 175 | 1,266 | 5,224 | gemini:gemini-3.5-flash | accepted |
| charlie | 3 | 0 | 0 | 0 | 0 | – | llm |
| delta | 3 | 6,252 | 1,247 | 0 | 7,499 | gemini:gemini-3.5-flash-lite → gemini:gemini-3.5-flash-lite | repaired |
| vega | 3 | 4,342 | 1,078 | 0 | 5,420 | gemini:gemini-3.5-flash | accepted |

Budget shares: charlie 9,006 / 14,700, delta 15,206 / 11,900, gamma 5,279 / 16,100, vega 13,412 / 13,300; reserve left 10,582 of 14,000.

Verdict under each persona's thresholds (same debate): zen Hold (6.7), balanced Hold (46.7), alpha Hold (46.7).

Gates: PASSED S +0.43 >= buy threshold +0.25; FAILED margin of safety -21.91 < +0.00; PASSED no volatility limit for this persona; FAILED S +0.43 > sell threshold -0.40.

Suggestions (computed playbook):

- **No action signal: hold or wait** (action): Neither the Buy nor the Sell rule is met under Alpha thresholds (debate score +0.43). Confidence in Hold 47%: the debate sits well inside the Hold zone.
- **Would become Buy if** (trigger): debate score reaches +0.25 (now +0.43); and price is at or below $17.03 (a 0% margin of safety on the DCF value $17.03; price now $390.22).
- **Would become Sell if** (trigger): the debate score falls to -0.40 or lower (now +0.43).
- **Fair-value range (Monte Carlo DCF)** (level): $7.59 to $39.20, median $16.83; the price $390.22 is above the whole range (0% of simulations put fair value above the price).
- **Analyst consensus target** (level): Mean target $412.48, +5.7% from the snapshot price.
- **Earnings ahead** (event): Next earnings report in 76 days. Options imply a move of about 18.8% either way by the first expiry after it; a decision made before the report carries that event risk.
- **Estimate revisions lean downward** (event): 12 upward and 16 downward EPS revisions for this fiscal year in the last 30 days.
- **Size for the swings** (risk): Over the past year, 1 trading day in 20 lost 4.07% or more, the worst peak-to-trough fall was 39.1%, and annualised volatility was 46%. Past swings, not a forecast.
- **Quant report disagrees** (context): Delta, Vega lean against the quant report's probabilities; check that disagreement before acting.
- **Partial committee** (context): Not counted: Gamma. The verdict rests on fewer domains than usual.

Sigma's explanation (model-written, verbatim):

> The committee rates TSLA a Hold with 47 % confidence. The debate score of +0.43 passes the buy‑threshold gate (+0.25) but fails the margin‑of‑safety gate (‑21.91 < 0), passes the volatility‑limit gate, and fails the sell‑threshold gate (score +0.43 > ‑0.40). Charlie’s data shows FY2024 revenue of $97.69 billion, a 17.9 % gross‑profit margin, $14.923 billion operating cash flow, $6.584 billion long‑term debt and an 8.0 % debt‑to‑equity ratio. He flags a revenue plateau, shrinking margins and rising debt as risks. Delta adds regulatory worries around a Tesla‑SpaceX merger and notes the Cybertruck production winding down. The fair‑value Monte Carlo DCF range is $7.59‑$39.20 with a median of $16.83, far below the current $390.22 price, and no simulations place fair value above price. Analyst consensus targets $412.48, a modest 5.7 % upside. Earnings are due in 76 days, with options pricing implying an 18.8 % move risk, and recent EPS revisions tilt slightly downward (12 up, 16 down). Past volatility has been high, with a 39.1 % peak‑to‑trough drop. Investors should watch the upcoming earnings report, any regulatory developments on the merger, Cybertruck production updates, and whether the debate score shifts toward the sell threshold.

## 1b. Analyses run from the dashboard (same build, real data)

Two analyses started by hand from the dashboard during the test window, read back from the session store.

| Ticker | Persona | Verdict | Confidence | Debate score | Faithfulness | Integrity | Tokens | Duration |
|---|---|---|---|---|---|---|---|---|
| ADANIENT | balanced | **No Verdict** | – | +0.000 | 0.0 | 0.0 | 0 | 3s |
| AMD | alpha | **Hold** | 63.6% in Hold | +0.038 | 95.0 | 75.0 | 61,362 | 237s |

**ADANIENT** (balanced): No verdict for ADANIENT: INSUFFICIENT_EVIDENCE: data available for 1 agent(s) (delta); 2 needed
Outcome INSUFFICIENT_EVIDENCE: data available for 1 agent(s) (delta); 2 needed.
Data warnings: financials unavailable: SEC financial data unavailable: ticker ADANIENT not found in SEC ticker map; market unavailable: yfinance market data unavailable: 'currentTradingPeriod'; catalysts unavailable: no market snapshot to price catalysts against.

- **No signal**: No recommendation was computed (INSUFFICIENT_EVIDENCE: data available for 1 agent(s) (delta); 2 needed). Nothing here supports acting either way; rerun when the missing data or agents are available.

**AMD** (alpha): Analysis complete for AMD. Recommendation: Hold (64% confidence in Hold)
Steps: charlie r1 3,719 (openai/gpt-oss-120b); delta r1 3,306 (gemini-3.5-flash-lite); gamma r1 5,399 (openai/gpt-oss-120b); vega r1 2,497 (gemini-3.5-flash); charlie r2 7,177 (openai/gpt-oss-120b → openai/gpt-oss-120b); delta r2 5,509 (gemini-3.5-flash-lite); gamma r2 7,225 (openai/gpt-oss-120b); vega r2 4,832 (gemini-3.5-flash); charlie r3 7,038 (openai/gpt-oss-120b); delta r3 8,635 (gemini-3.5-flash-lite → gemini-3.5-flash-lite); gamma r3 0 (budget); vega r3 6,025 (openai/gpt-oss-20b).
Stances (round 1 → round 3): charlie bullish → bullish, delta neutral → neutral, gamma bearish → –, vega bullish → bullish.

- **No action signal: hold or wait**: Neither the Buy nor the Sell rule is met under Alpha thresholds (debate score +0.04). Confidence in Hold 64%: the debate sits well inside the Hold zone.
- **Would become Buy if**: debate score reaches +0.25 (now +0.04); and price is at or below $55.62 (a 0% margin of safety on the DCF value $55.62; price now $534.06).
- **Would become Sell if**: the debate score falls to -0.40 or lower (now +0.04).
- **Fair-value range (Monte Carlo DCF)**: $14.61 to $99.46, median $53.50; the price $534.06 is above the whole range (0% of simulations put fair value above the price).
- **Analyst consensus target**: Mean target $657.74, +23.2% from the snapshot price.
- **Earnings ahead**: Next earnings report in 82 days. Options imply a move of about 25.7% either way by the first expiry after it; a decision made before the report carries that event risk.
- **Estimate revisions lean upward**: 32 upward and 1 downward EPS revisions for this fiscal year in the last 30 days.
- **Size for the swings**: Over the past year, 1 trading day in 20 lost 6.68% or more, the worst peak-to-trough fall was 37.7%, and annualised volatility was 73%. Past swings, not a forecast.
- **Quant report disagrees**: Charlie, Vega lean against the quant report's probabilities; check that disagreement before acting.

## 2. What the live runs showed

**Fact-check reproduced independently.** Re-running the verifier on every round-1 claim gave the same result as the debate:

- NVDA: charlie 3/3; delta 4/4; gamma 4/4; vega 5/5
- AAPL: charlie 3/3; delta 3/3; gamma 5/5; vega 4/4
- TSLA: charlie 5/6 (failed: C2: value_mismatch, C7: unsupported_inference); delta 3/3; gamma 1/2 (failed: C2: ungrounded_number, C3: unsupported_inference); vega 6/6

**Agents stayed in their lane.** Round-3 stance changes and what justified them:

- NVDA: no stance changes
- AAPL: no stance changes
- TSLA: no stance changes

**Sigma's explanation.** Numbers in the text that could not be traced to the evidence, the gates, the playbook or the quant report:

- NVDA: 171 words; untraced numbers: none
- AAPL: 209 words; untraced numbers: 33 percent
- TSLA: 195 words; untraced numbers: 17.9 %, 8.0 %

**Human approval rule on the real verdicts:**

- NVDA: Hold 9.64: info notification, no approval needed
- AAPL: Hold 73.5: info notification, no approval needed
- TSLA: Hold 46.67: info notification, no approval needed

## 3. Live data checks

- NVDA snapshot: $162.85 fetched 2026-11-14T05:03:01Z (last close 2026-11-13).
- NVDA price returns, each with its window: 5d -12.7% (2026-11-06..2026-11-13); 1m -25.0% (2026-10-15..2026-11-13); 3m -27.5% (2026-08-17..2026-11-13); 6m -27.6% (2026-05-15..2026-11-13); 1y -15.8% (2025-11-12..2026-11-13); drawdown_from_52w_high -31.9% (2026-10-06..2026-11-13).
- NVDA diluted EPS on today's share basis (2024 10-for-1 split applied to older filings): FY2022 0.385, FY2023 0.17, FY2024 1.19, FY2025 2.94, FY2026 4.9; growth +88.9% a year (was reported as 6.2% before the fix).
- NVDA TTM EPS 7.91 for 2025-07-26..2026-07-26 (FY to 2026-01-25 + 6M to 2026-07-26 - 6M to 2025-07-27; split-adjusted).
- NVDA catalysts: days_to_earnings 5.0000 (2026-11-13..2026-11-18); eps_estimate_next 2.4712 (2026-11-18); target_mean 283.3076 (2026-11-13); target_mean_upside 0.7397 (2026-11-13); implied_vol_atm 0.9251 (2026-11-13..2026-11-18); implied_move 0.0863 (2026-11-13..2026-11-18); eps_revisions_up_30d 2.0000 (2026-10-14..2026-11-13); eps_revisions_down_30d 0.0000 (2026-10-14..2026-11-13).
- NVDA news: 15 articles, 14 with a summary; providers ok yahoo_rss, nasdaq_rss, google_rss; failed yfinance.

## 4. Every configured model, live, under the strict schema

| Model | Prompt | Output | Hidden reasoning | Total | Claims |
|---|---|---|---|---|---|
| gemini:gemini-3.5-flash-lite | 795 | 361 | 0 | 1156 | 3 |
| groq:openai/gpt-oss-120b | 1287 | 206 | 227 | 1720 | 3 |
| groq:openai/gpt-oss-20b | 1287 | 214 | 127 | 1628 | 3 |

## 4b. Dashboard tests

`npm test` in kai-frontend: **65 passed, 0 failed** of 65. The components are rendered with the real `/status` bodies of the three live debates above (kai-frontend/src/test/fixtures/), so every verdict, suggestion, claim and token figure they check is real output. The API-client and source-hygiene tests stub the network transport only; they contain no market or model data.

Gateway market endpoints, live: indices S&P 500, NASDAQ, DOW, VIX; NVDA quote 162.85; 161 daily points for one month.

## 5. Findings from this run and what was changed

Written after reading the run above (analysis, not generated). Changes listed here were made after the run; they still need a full re-run to be confirmed.

| Finding (evidence above) | Change made | Confirmed |
|---|---|---|
| Gamma skipped round 3 for lack of token budget in 3 of 4 four-agent debates (NVDA, AAPL here; AMD from the dashboard). Totals 61-62k of 70k. | Debate limit 70k → 80k; shares rebalanced to Gamma 25%, Charlie 20%, Delta 19%, Vega 16% (reserve 20%). | No: needs a full re-run |
| Most repairs were critiques or claims with an empty evidence list (the JSON schema allowed `[]`, Kai's validator does not). | Schemas now require at least one evidence id (critiques, numeric 1-2, quotes exactly 1, inference based_on ≥ 1). | Partly: gpt-oss-120b, gpt-oss-20b and gemini-3.5-flash-lite accept the new schema live (section 4) |
| Google free tier: gemini-2.5-flash and gemini-3.5-flash allow only 20 requests a day each (provider error text). Gamma's first choice was out of quota for most of the run; TSLA's Charlie lost round 3 when its fallback was also out. | A third model with a high quota (gemini-3.5-flash-lite or gpt-oss-120b) added to every agent's chain; daily-quota errors are no longer retried, the chain moves on at once. | No: needs a re-run |
| A Gemini request hung with no timeout and stalled the test run. | 120-second timeout on every Groq and Gemini request; a timed-out model fails over to the next. | Yes: the model re-run completed in 11 s |
| Delta needed a repair in round 3 in every debate (it left some critiques unanswered). | None yet; the repair succeeded each time (cost about 4k tokens per debate). | Open |
| ADANIENT (Indian NSE listing) ended as No Verdict / insufficient evidence: not in SEC's ticker map, and yfinance needs the `.NS` suffix. | None: correct behaviour for a non-US ticker (Kai does not guess). Non-US support would need other data sources. | As designed |

## 6. Every test

| Result | Test | What it checks | Time |
|---|---|---|---|
| pass | `test_market_snapshot_has_a_fetch_time_and_matching_price` | The market snapshot records when it was fetched and the last close it belongs to. | 0.0s |
| pass | `test_return_windows_are_dated_and_recompute_from_closes` | Every price return carries its exact date range and equals last close / start close - 1. | 0.0s |
| pass | `test_sec_eps_is_split_adjusted_for_filings_before_a_split` | NVDA split 10-for-1 in June 2024: EPS from filings made before it must be divided by 10. | 0.0s |
| pass | `test_ttm_eps_is_built_from_the_latest_filings` | TTM EPS covers twelve months ending at the latest 10-Q or 10-K. | 0.0s |
| pass | `test_catalysts_are_priced_off_the_snapshot` | Earnings date, analyst targets and options are fetched; ratios use the snapshot price. | 1.77s |
| pass | `test_sec_financials_and_market_data_for_other_tickers[AAPL]` | SEC statements (five fiscal years plus TTM) and market data load for each test ticker. | 2.71s |
| pass | `test_sec_financials_and_market_data_for_other_tickers[TSLA]` | SEC statements (five fiscal years plus TTM) and market data load for each test ticker. | 2.97s |
| pass | `test_news_comes_from_several_feeds_with_summaries` | News is merged from several providers; most articles carry a summary, not just a title. | 0.98s |
| pass | `test_analysis_finishes_with_a_verdict_or_a_stated_reason[NVDA]` | The analysis ends as completed or waiting for approval, with Buy/Hold/Sell or a reasoned No Verdict. | 0.0s |
| pass | `test_analysis_finishes_with_a_verdict_or_a_stated_reason[AAPL]` | The analysis ends as completed or waiting for approval, with Buy/Hold/Sell or a reasoned No Verdict. | 0.0s |
| pass | `test_analysis_finishes_with_a_verdict_or_a_stated_reason[TSLA]` | The analysis ends as completed or waiting for approval, with Buy/Hold/Sell or a reasoned No Verdict. | 0.0s |
| pass | `test_tokens_stay_within_the_limit_and_each_agents_share[NVDA]` | Total tokens never exceed the limit; the trace adds up; no agent spends more than its share plus the reserve. | 0.0s |
| pass | `test_tokens_stay_within_the_limit_and_each_agents_share[AAPL]` | Total tokens never exceed the limit; the trace adds up; no agent spends more than its share plus the reserve. | 0.0s |
| pass | `test_tokens_stay_within_the_limit_and_each_agents_share[TSLA]` | Total tokens never exceed the limit; the trace adds up; no agent spends more than its share plus the reserve. | 0.0s |
| pass | `test_all_four_agents_ran_on_their_configured_models[NVDA]` | Charlie, Delta, Gamma and Vega each ran round 1 (or failed with an explicit reason), each on its own model chain. | 0.0s |
| pass | `test_all_four_agents_ran_on_their_configured_models[AAPL]` | Charlie, Delta, Gamma and Vega each ran round 1 (or failed with an explicit reason), each on its own model chain. | 0.0s |
| pass | `test_all_four_agents_ran_on_their_configured_models[TSLA]` | Charlie, Delta, Gamma and Vega each ran round 1 (or failed with an explicit reason), each on its own model chain. | 0.0s |
| pass | `test_every_agent_used_the_same_market_snapshot[NVDA]` | Every agent's price is the snapshot price, and P/E = snapshot price / TTM EPS. | 0.0s |
| pass | `test_every_agent_used_the_same_market_snapshot[AAPL]` | Every agent's price is the snapshot price, and P/E = snapshot price / TTM EPS. | 0.0s |
| pass | `test_every_agent_used_the_same_market_snapshot[TSLA]` | Every agent's price is the snapshot price, and P/E = snapshot price / TTM EPS. | 0.0s |
| pass | `test_fact_check_reproduces_independently[NVDA]` | Re-running the verifier on every agent's round-1 claims gives exactly the stored fact-check. | 0.0s |
| pass | `test_fact_check_reproduces_independently[AAPL]` | Re-running the verifier on every agent's round-1 claims gives exactly the stored fact-check. | 0.0s |
| pass | `test_fact_check_reproduces_independently[TSLA]` | Re-running the verifier on every agent's round-1 claims gives exactly the stored fact-check. | 0.0s |
| pass | `test_agents_stay_in_their_lane[NVDA]` | A stance change in round 3 rests on a dispute of the agent's own claims or on its own failed claims. | 0.0s |
| pass | `test_agents_stay_in_their_lane[AAPL]` | A stance change in round 3 rests on a dispute of the agent's own claims or on its own failed claims. | 0.0s |
| pass | `test_agents_stay_in_their_lane[TSLA]` | A stance change in round 3 rests on a dispute of the agent's own claims or on its own failed claims. | 0.0s |
| pass | `test_no_accepted_dispute_compares_different_return_windows[NVDA]` | Round-2 disputes never pit a return over one window against a return over another. | 0.0s |
| pass | `test_no_accepted_dispute_compares_different_return_windows[AAPL]` | Round-2 disputes never pit a return over one window against a return over another. | 0.0s |
| pass | `test_no_accepted_dispute_compares_different_return_windows[TSLA]` | Round-2 disputes never pit a return over one window against a return over another. | 0.0s |
| pass | `test_verdict_and_confidence_recompute_from_the_scores[NVDA]` | S is the judge-weighted mean of agent scores; confidence follows the published formula, Hold included. | 0.0s |
| pass | `test_verdict_and_confidence_recompute_from_the_scores[AAPL]` | S is the judge-weighted mean of agent scores; confidence follows the published formula, Hold included. | 0.0s |
| pass | `test_verdict_and_confidence_recompute_from_the_scores[TSLA]` | S is the judge-weighted mean of agent scores; confidence follows the published formula, Hold included. | 0.0s |
| pass | `test_suggestions_numbers_come_from_the_evidence[NVDA]` | Each playbook number that has a source is recomputed from that source (entry price, earnings days). | 0.0s |
| pass | `test_suggestions_numbers_come_from_the_evidence[AAPL]` | Each playbook number that has a source is recomputed from that source (entry price, earnings days). | 0.0s |
| pass | `test_suggestions_numbers_come_from_the_evidence[TSLA]` | Each playbook number that has a source is recomputed from that source (entry price, earnings days). | 0.0s |
| pass | `test_explanation_names_the_computed_verdict[NVDA]` | Sigma's explanation states the computed recommendation; numbers it uses that are not in the material are counted. | 0.0s |
| pass | `test_explanation_names_the_computed_verdict[AAPL]` | Sigma's explanation states the computed recommendation; numbers it uses that are not in the material are counted. | 0.0s |
| pass | `test_explanation_names_the_computed_verdict[TSLA]` | Sigma's explanation states the computed recommendation; numbers it uses that are not in the material are counted. | 0.0s |
| pass | `test_signed_packet_verifies[NVDA]` | The alpha packet's Ed25519 signature verifies and it carries the verdict and suggestions. | 0.0s |
| pass | `test_signed_packet_verifies[AAPL]` | The alpha packet's Ed25519 signature verifies and it carries the verdict and suggestions. | 0.0s |
| pass | `test_signed_packet_verifies[TSLA]` | The alpha packet's Ed25519 signature verifies and it carries the verdict and suggestions. | 0.0s |
| pass | `test_market_endpoints_return_real_quotes` | Indices, quote, price history and news come from the providers (no substitute numbers). | 3.87s |
| pass | `test_history_lists_every_live_run` | Every analysis the suite ran is in the stored history with its recommendation. | 0.01s |
| pass | `test_human_approval_rule_on_the_real_verdict[NVDA]` | Buy/Sell at confidence >= 60 waits for approval (then approving completes it); anything else notifies only. | 0.0s |
| pass | `test_human_approval_rule_on_the_real_verdict[AAPL]` | Buy/Sell at confidence >= 60 waits for approval (then approving completes it); anything else notifies only. | 0.0s |
| pass | `test_human_approval_rule_on_the_real_verdict[TSLA]` | Buy/Sell at confidence >= 60 waits for approval (then approving completes it); anything else notifies only. | 0.0s |
| pass | `test_every_prompt_file_loads` | Every agent's system prompt loads from prompts/ with its shared rules included. | 0.0s |
| **FAIL** | `test_model_answers_under_the_strict_schema[gemini:gemini-2.5-flash]` | The model replies with JSON that passes the schema and Kai's validation, citing real ids. | 1.68s |
| **FAIL** | `test_model_answers_under_the_strict_schema[gemini:gemini-3.5-flash]` | The model replies with JSON that passes the schema and Kai's validation, citing real ids. | 0.45s |
| pass | `test_model_answers_under_the_strict_schema[gemini:gemini-3.5-flash-lite]` | The model replies with JSON that passes the schema and Kai's validation, citing real ids. | 1.43s |
| pass | `test_model_answers_under_the_strict_schema[groq:openai/gpt-oss-120b]` | The model replies with JSON that passes the schema and Kai's validation, citing real ids. | 1.85s |
| pass | `test_model_answers_under_the_strict_schema[groq:openai/gpt-oss-20b]` | The model replies with JSON that passes the schema and Kai's validation, citing real ids. | 0.9s |
| pass | `test_debaters_start_on_different_models` | The four debating agents start on four different models (rate limits spread, no echo chamber). | 0.0s |

### Failures

**tests/test_models_live.py::test_model_answers_under_the_strict_schema[gemini:gemini-2.5-flash]**

```
= max_retries - 1:
                break
            wait = _retry_delay(failures) or 5 * (2 ** attempt)
            print(f"[LLM] {self.role}: every model rate-limited or overloaded; retrying in {wait:.0f}s")
            time.sleep(min(wait, 60) + random.uniform(0, 1))
        if not failures:
            raise ValueError("No LLM configured: set GROQ_API_KEY and/or GOOGLE_API_KEY.")
>       raise RuntimeError(f"all models failed for {self.role}: " + " | ".join(failures))
E       RuntimeError: all models failed for charlie: gemini:gemini-2.5-flash: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://aistudio.google.com/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash, description: Generate content requests per day limit exceeded on Free Tier. See https://aistudio.google.com/docs/rate-limits.\nPlease retry in 2h25m7.137505677s.', 'st

coordinator/llm_client.py:194: RuntimeError
```

**tests/test_models_live.py::test_model_answers_under_the_strict_schema[gemini:gemini-3.5-flash]**

```
= max_retries - 1:
                break
            wait = _retry_delay(failures) or 5 * (2 ** attempt)
            print(f"[LLM] {self.role}: every model rate-limited or overloaded; retrying in {wait:.0f}s")
            time.sleep(min(wait, 60) + random.uniform(0, 1))
        if not failures:
            raise ValueError("No LLM configured: set GROQ_API_KEY and/or GOOGLE_API_KEY.")
>       raise RuntimeError(f"all models failed for {self.role}: " + " | ".join(failures))
E       RuntimeError: all models failed for charlie: gemini:gemini-3.5-flash: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://aistudio.google.com/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-3.6-flash, description: Generate content requests per day limit exceeded on Free Tier. See https://aistudio.google.com/docs/rate-limits.\nPlease retry in 2h25m6.675572276s.', 'st

coordinator/llm_client.py:194: RuntimeError
```

## Not covered by these tests

- Browsers other than the headless Chromium used for the UI checks; the dashboard is tested with real `/status` payloads captured from these runs, not by clicking through a live browser here.
- Calibration: whether stated probabilities match outcomes needs `scripts/calibration_backtest.py` over years of data.
- Scenarios that real data did not produce in this run (for example a circuit-breaker stop or a strong Buy/Sell needing approval) are reported above only if they occurred.
