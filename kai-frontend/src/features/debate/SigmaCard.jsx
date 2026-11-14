import React, { useEffect, useState } from 'react';
import { AlertTriangle, CalendarClock, CheckCircle2, Circle, Compass, Info, MinusCircle, Ruler, ShieldAlert, ShieldCheck, TrendingDown, TrendingUp } from 'lucide-react';
import { useCommittee } from '../../context/committee';
import Chip from '../../components/Chip';
import { getQuote } from '../../services/api';
import { AGENTS, DASH, score } from '../../utils/format';

const SIGNAL = {
    Buy: { icon: TrendingUp, tone: 'pos' },
    Sell: { icon: TrendingDown, tone: 'neg' },
    Hold: { icon: MinusCircle, tone: 'neutral' },
    'No Verdict': { icon: AlertTriangle, tone: 'warn' },
};

const conf = (c) => (c === null || c === undefined ? DASH : `${Math.round(c)}%`);

/** Real quote of the ticker (or an honest "unavailable" with the reason). */
const LivePrice = ({ ticker }) => {
    const [state, setState] = useState({ loading: true });
    useEffect(() => {
        let cancelled = false;
        getQuote(ticker)
            .then((quote) => !cancelled && setState({ quote }))
            .catch((error) => !cancelled && setState({ error: error.message }));
        return () => { cancelled = true; };
    }, [ticker]);

    if (state.loading) return <div className="small text-muted">Loading quote…</div>;
    if (state.error) return <div className="small text-muted">Quote unavailable: {state.error}</div>;
    const { quote } = state;
    return (
        <div>
            <div style={{ fontSize: '1.75rem', fontWeight: 700, letterSpacing: '-0.02em', lineHeight: 1.15 }}>${quote.price}</div>
            <div className={`small fw-semibold ${quote.is_positive ? 'pos-text' : 'neg-text'}`}>{quote.change} today</div>
        </div>
    );
};

const Meter = ({ label, value }) => (
    <div className="mb-3">
        <div className="d-flex justify-content-between mb-1">
            <span className="small" style={{ color: 'var(--text-2)' }}>{label}</span>
            <span className="small fw-semibold">{score(value)}</span>
        </div>
        <div className="kai-meter"><span style={{ width: `${Math.max(0, Math.min(100, value))}%` }} /></div>
    </div>
);

// How each kind of suggestion is shown (judge/playbook.py Suggestion.kind)
const KIND = {
    action: { icon: Compass, tone: 'accent', label: 'Signal' },
    trigger: { icon: Info, tone: 'info', label: 'Trigger' },
    level: { icon: Ruler, tone: 'plain', label: 'Level' },
    event: { icon: CalendarClock, tone: 'warn', label: 'Event' },
    risk: { icon: ShieldAlert, tone: 'neg', label: 'Risk' },
    context: { icon: Info, tone: 'neutral', label: 'Context' },
};

/** The computed playbook: what the verdict means, what would change it, levels, events, risk. */
const Playbook = ({ items }) => (
    <div data-testid="playbook">
        <div className="kai-label">What to watch <span className="text-lowercase fw-normal" style={{ letterSpacing: 0 }}>(computed from the evidence; research, not advice)</span></div>
        <div className="row g-3">
            {items.map((s, i) => {
                const k = KIND[s.kind] || KIND.context;
                const Icon = k.icon;
                return (
                    <div key={`${s.kind}-${i}`} className={s.kind === 'action' ? 'col-12' : 'col-md-6'}>
                        <div className="kai-tile h-100" title={s.sources?.length ? `From: ${s.sources.join(', ')}` : undefined}>
                            <div className="d-flex align-items-center gap-2 mb-1">
                                <Icon size={16} style={{ color: 'var(--text-2)' }} />
                                <span className="fw-semibold">{s.title}</span>
                                <Chip tone={k.tone} className="ms-auto">{k.label}</Chip>
                            </div>
                            <div className="small" style={{ color: 'var(--text-2)' }}>{s.detail}</div>
                        </div>
                    </div>
                );
            })}
        </div>
    </div>
);

/** Sigma's verdict. Every number here was computed in code; the LLM only wrote the narrative. */
const SigmaCard = () => {
    const { session, sigma, debate } = useCommittee();
    const snapshot = debate?.market_snapshot;
    if (!sigma) return null;

    const rec = sigma.final_recommendation;
    const quality = sigma.evaluation;
    const sig = SIGNAL[rec.recommendation] || SIGNAL['No Verdict'];
    const Icon = sig.icon;
    const hasConfidence = rec.confidence !== null && rec.confidence !== undefined;
    const adjusted = hasConfidence && rec.confidence_before_quant !== null && rec.confidence_before_quant !== undefined
        && Math.round(rec.confidence_before_quant) !== Math.round(rec.confidence);

    return (
        <section className="kai-card kai-fade" data-testid="sigma-card">
            <div className="kai-card-head">
                <div>
                    <div className="kai-label mb-1">Final verdict · {sigma.persona} thresholds</div>
                    <h2 className="mb-0" style={{ fontSize: '1.5rem' }}>{sigma.ticker}</h2>
                    {snapshot?.fetched_at && (
                        <div className="small text-muted" data-testid="snapshot">
                            Market snapshot {snapshot.fetched_at.replace('T', ' ').replace('Z', ' UTC')} · last close {snapshot.market_date}
                        </div>
                    )}
                </div>
                {session?.alpha_packet && (
                    <Chip tone="plain" title="Ed25519-signed record of this verdict, its inputs and the quant report">
                        <ShieldCheck size={14} /> Signed alpha packet
                    </Chip>
                )}
            </div>

            <div className="kai-card-body">
                <div className="row g-4">
                    <div className="col-lg-5">
                        <div className="kai-label">Recommendation</div>
                        <div className={`kai-verdict-pill ${sig.tone}`} data-testid="verdict"><Icon size={30} /> {rec.recommendation}</div>
                        <div className="mt-3 d-flex align-items-baseline gap-2 flex-wrap" data-testid="confidence">
                            {hasConfidence
                                ? <><span style={{ fontSize: '1.5rem', fontWeight: 700 }}>{conf(rec.confidence)}</span>{' '}
                                    <span className="text-muted">{rec.recommendation === 'Hold' ? 'confidence in Hold' : 'confidence'}</span></>
                                : <span className="text-muted">No confidence: no verdict was reached</span>}
                        </div>
                        {adjusted && (
                            <div className="small text-muted mt-1">
                                {conf(rec.confidence_before_quant)} before the quant report × agreement {rec.quant_agreement?.toFixed(2)}
                                {' '}× contradictions {rec.contradiction_factor?.toFixed(2)}
                            </div>
                        )}
                        {rec.recommendation === 'Hold' && rec.hold_conviction != null && (
                            <div className="small text-muted mt-1">
                                {rec.hold_conviction >= 0.5 ? 'Firmly inside the Hold zone' : 'Close to a Buy or Sell trigger'}
                                {' '}(conviction {rec.hold_conviction.toFixed(2)}{rec.coverage != null ? `, domains counted ${Math.round(rec.coverage * 100)}%` : ''})
                            </div>
                        )}
                        <div className="small text-muted mt-1">Debate score {rec.score >= 0 ? '+' : ''}{rec.score.toFixed(2)} on a scale of −1 (bearish) to +1 (bullish)</div>
                    </div>

                    <div className="col-lg-4">
                        <div className="kai-label">Trust &amp; verification</div>
                        <Meter label="Argument faithfulness" value={quality.faithfulness_score} />
                        <Meter label="Debate integrity" value={quality.integrity_score} />
                        <div className="small text-muted">Faithfulness: share of numeric claims that match the fetched data. Integrity: critiques answered and failed claims corrected.</div>
                    </div>

                    <div className="col-lg-3">
                        <div className="kai-label">Last price</div>
                        <LivePrice ticker={sigma.ticker} />
                    </div>
                </div>

                {rec.contradictions.length > 0 && (
                    <div className="kai-alert warn mt-4" role="alert" data-testid="contradictions">
                        <strong>Contradicts the quant report.</strong> Confidence was reduced.
                        <ul className="mb-0 mt-1 ps-3">
                            {rec.contradictions.map((c) => (
                                <li key={`${c.agent}-${c.field}`}>
                                    {AGENTS[c.agent]?.name || c.agent} is {c.stance}, but “{debate?.quant_report?.fields?.[c.field]?.label || c.field}” is {(c.value * 100).toFixed(0)}%
                                </li>
                            ))}
                        </ul>
                    </div>
                )}
            </div>

            <hr className="kai-divider" />
            <div className="kai-card-body">
                <div className="row g-4">
                    <div className="col-md-6">
                        <div className="kai-label">Decision gates</div>
                        <ul className="list-unstyled mb-0 small">
                            {rec.gates.map((g) => (
                                <li key={g.name} className="d-flex gap-2 align-items-start mb-2">
                                    {g.passed
                                        ? <CheckCircle2 size={16} className="pos-text flex-shrink-0 mt-1" />
                                        : <Circle size={16} className="text-muted flex-shrink-0 mt-1" />}
                                    <span>{g.note}</span>
                                </li>
                            ))}
                        </ul>
                    </div>
                    <div className="col-md-6">
                        <div className="kai-label">Same debate, each persona’s thresholds</div>
                        <table className="kai-table">
                            <thead><tr><th>Persona</th><th>Verdict</th><th className="num">Confidence</th></tr></thead>
                            <tbody>
                                {Object.entries(sigma.verdicts_by_persona).map(([p, v]) => (
                                    <tr key={p} style={p === sigma.persona ? { fontWeight: 650 } : undefined}>
                                        <td className="text-capitalize">{p}{p === sigma.persona && <span className="muted"> (selected)</span>}</td>
                                        <td>{v.recommendation}</td>
                                        <td className="num">{conf(v.confidence)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    <div className="col-md-6">
                        <div className="kai-label">Verified supporting points</div>
                        {rec.supporting_points.length
                            ? <ul className="small mb-0 ps-3">{rec.supporting_points.map((p) => <li key={p}>{p}</li>)}</ul>
                            : <span className="small text-muted">none</span>}
                    </div>
                    <div className="col-md-6">
                        <div className="kai-label">Risks raised by the agents</div>
                        {rec.key_risks.length
                            ? <ul className="small mb-0 ps-3">{rec.key_risks.map((r) => <li key={r}>{r}</li>)}</ul>
                            : <span className="small text-muted">none</span>}
                    </div>
                </div>
            </div>

            {(rec.agents_missing.length > 0 || rec.warnings.length > 0 || rec.reason) && (
                <div className="px-4 pb-3">
                    <div className="kai-alert warn" role="status" data-testid="verdict-warnings">
                        {rec.reason && <div><strong>No verdict:</strong> {rec.reason}</div>}
                        {rec.agents_missing.length > 0 && (
                            <div><strong>Verdict based on fewer agents.</strong> Not counted: {rec.agents_missing.join(', ')}</div>
                        )}
                        {rec.warnings.length > 0 && <ul className="mb-0 mt-1 ps-3">{rec.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
                    </div>
                </div>
            )}

            {rec.suggestions?.length > 0 && (
                <>
                    <hr className="kai-divider" />
                    <div className="kai-card-body"><Playbook items={rec.suggestions} /></div>
                </>
            )}

            <hr className="kai-divider" />
            <div className="kai-card-body">
                <div className="kai-label">Explanation <span className="text-lowercase fw-normal" style={{ letterSpacing: 0 }}>(written by the LLM; it cannot change the verdict)</span></div>
                <p className="mb-0" style={{ whiteSpace: 'pre-wrap', color: 'var(--text)' }}>{rec.analysis}</p>
            </div>
        </section>
    );
};

export default SigmaCard;
