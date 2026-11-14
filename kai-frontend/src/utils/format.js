// Display helpers. Nothing here invents a value: a missing value renders as a dash.

export const DASH = '—';

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

/** 478834000000 -> "478.83B", 14532182000 -> "14.53B", 354.88 -> "354.9", 0.0603 -> "0.0603". */
export const compact = (v) => {
    if (!isNum(v)) return DASH;
    const a = Math.abs(v);
    if (a >= 1e12) return `${(v / 1e12).toFixed(2)}T`;
    if (a >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
    if (a >= 1000) return v.toLocaleString('en-US', { maximumFractionDigits: 0 });
    return String(Number(v.toPrecision(4)));
};

/** A claim or evidence value with its unit exactly as stated, e.g. "478.83B USD". */
export const withUnit = (v, unit) => (isNum(v) ? `${compact(v)}${unit ? ` ${unit}` : ''}` : DASH);

export const pct = (v, digits = 1) => (isNum(v) ? `${(v * 100).toFixed(digits)}%` : DASH);
export const num = (v, digits = 2) => (isNum(v) ? v.toFixed(digits) : DASH);
export const usd = (v, digits = 2) => (isNum(v)
    ? `$${v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`
    : DASH);
export const score = (v) => (isNum(v) ? `${Math.round(v)}%` : DASH);

export const clock = (iso) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? DASH : d.toLocaleTimeString();
};

export const dateTime = (iso) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? DASH : d.toLocaleString();
};

export const AGENTS = {
    charlie: { name: 'Charlie', role: 'Fundamentals · SEC filings' },
    delta: { name: 'Delta', role: 'Sentiment · news' },
    gamma: { name: 'Gamma', role: 'Valuation & risk' },
    vega: { name: 'Vega', role: 'Catalysts & options' },
};
export const AGENT_IDS = Object.keys(AGENTS);

/** Chip tone for each stance. */
export const STANCE_TONE = { bullish: 'pos', neutral: 'neutral', bearish: 'neg' };

/** Human wording for the reasons a debate can end without a verdict. */
export const NO_VERDICT_TITLES = {
    CIRCUIT_BREAKER_FAITHFULNESS: 'Circuit breaker: too few agents passed the fact-check',
    INSUFFICIENT_EVIDENCE: 'Not enough data to start a debate',
    INSUFFICIENT_AGENTS: 'Too few agents produced a valid analysis',
    TOKEN_LIMIT: 'Stopped: token budget used up',
};
