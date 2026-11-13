import React from 'react';
import { CheckCircle2, MinusCircle, XCircle } from 'lucide-react';
import Chip from '../../components/Chip';
import CitationPopover from '../../components/CitationPopover';
import { AGENTS, STANCE_TONE, pct, score, withUnit } from '../../utils/format';

const VERDICT_TONE = { dispute: 'neg', qualify: 'warn', support: 'pos' };
const ACTION_TONE = { kept: 'neutral', revised: 'warn', withdrawn: 'neg' };

export const StanceBadge = ({ stance, confidence }) => (
    <Chip tone={STANCE_TONE[stance] || 'neutral'} className="text-uppercase">
        {stance}{confidence !== undefined && confidence !== null ? ` · ${Math.round(confidence)}` : ''}
    </Chip>
);

/** Whether the code-side check against the fetched data passed for one claim. */
const CheckMark = ({ check }) => {
    if (!check) return null;
    if (check.verified) {
        return (
            <span className="pos-text small fw-semibold d-inline-flex align-items-center gap-1" title="Verified against the fetched data">
                <CheckCircle2 size={14} /> verified
            </span>
        );
    }
    return (
        <span className="neg-text small fw-semibold d-inline-flex align-items-center gap-1" title={check.detail || check.reason}>
            <XCircle size={14} /> failed: {check.reason}
        </span>
    );
};

const Claim = ({ claim, check, evidence }) => (
    <li className="py-3" style={{ borderBottom: '1px solid var(--border)' }}>
        <div className="d-flex flex-wrap align-items-center gap-2 mb-1">
            <Chip mono>{claim.id}</Chip>
            <span className="kai-label mb-0">{claim.kind}</span>
            <CheckMark check={check} />
            {check && !check.checkable && check.verified && (
                <span className="text-muted small d-inline-flex align-items-center gap-1"><MinusCircle size={13} /> not a data claim</span>
            )}
        </div>
        <div style={{ color: 'var(--text)' }}>{claim.text}</div>
        {claim.kind === 'numeric' && (
            <div className="small text-muted mt-1">
                states <span className="font-monospace" style={{ color: 'var(--text)' }}>{withUnit(claim.value, claim.unit)}</span> ({claim.op})
                {check && !check.verified && check.expected !== null && check.expected !== undefined && (
                    <span className="neg-text"> · the data gives {withUnit(check.expected, '')}</span>
                )}
            </div>
        )}
        {claim.kind === 'quote' && (
            <blockquote className="small ps-3 my-2 text-muted" style={{ borderLeft: '3px solid var(--border-strong)', fontStyle: 'italic' }}>“{claim.quote}”</blockquote>
        )}
        {claim.kind === 'inference' && <div className="small text-muted mt-1">based on {claim.based_on.join(', ')}</div>}
        {claim.evidence?.length > 0 && (
            <div className="mt-2 d-flex flex-wrap align-items-center gap-1">
                <span className="small text-muted me-1">evidence:</span>
                {claim.evidence.map((id) => <CitationPopover key={id} evidenceId={id} evidence={evidence} />)}
            </div>
        )}
        {check && !check.verified && check.detail && <div className="small neg-text mt-1">{check.detail}</div>}
    </li>
);

const ClaimList = ({ claims, verification, evidence }) => {
    const checks = Object.fromEntries((verification?.checks || []).map((c) => [c.claim_id, c]));
    return (
        <ul className="list-unstyled mb-0" style={{ borderTop: '1px solid var(--border)' }}>
            {claims.map((c) => <Claim key={c.id} claim={c} check={checks[c.id]} evidence={evidence} />)}
        </ul>
    );
};

const Section = ({ title, children }) => (
    <div className="mb-4">
        <div className="kai-label">{title}</div>
        {children}
    </div>
);

const Risks = ({ risks }) => (risks?.length ? (
    <ul className="small mb-0 ps-3">{risks.map((r) => <li key={r}>{r}</li>)}</ul>
) : <span className="small text-muted">none stated</span>);

/** Everything one agent said across the three rounds, and what the checks found. */
const AgentPanel = ({ agent }) => {
    const meta = AGENTS[agent.agent];
    const evidence = Object.fromEntries((agent.evidence || []).map((e) => [e.id, e]));
    const { round1, round2, round3 } = agent;

    return (
        <div className="pt-4" data-testid={`agent-panel-${agent.agent}`}>
            {agent.state === 'failed' && (
                <div className="kai-alert neg mb-3" role="alert"><strong>{meta.name} failed.</strong> {agent.error}</div>
            )}
            {agent.state === 'dropped' && (
                <div className="kai-alert warn mb-3" role="alert">
                    <strong>{meta.name} was dropped by the circuit breaker</strong>
                    {agent.dropped_reason ? `: ${agent.dropped_reason}` : '.'} Its claims do not count towards the verdict.
                </div>
            )}
            {!round1 && agent.state !== 'failed' && <div className="text-muted small">{meta.name} has not produced a result yet.</div>}

            {round1 && (
                <Section title="Round 1: independent analysis">
                    <div className="d-flex flex-wrap align-items-center gap-2 mb-3">
                        <StanceBadge stance={round1.stance} confidence={round1.confidence} />
                        {agent.verification && (
                            <span className="small text-muted">
                                fact-check: {agent.verification.verified}/{agent.verification.checkable} data claims verified
                                {' '}({score(agent.verification.faithfulness * 100)} faithful)
                            </span>
                        )}
                    </div>
                    <ClaimList claims={round1.claims} verification={agent.verification} evidence={evidence} />
                    <div className="mt-3"><span className="small text-muted">Risks:</span> <Risks risks={round1.risks} /></div>
                </Section>
            )}

            {round2 && (
                <Section title="Round 2: challenges and confirmations">
                    <ul className="list-unstyled mb-0" style={{ borderTop: '1px solid var(--border)' }}>
                        {round2.critiques.map((k) => (
                            <li key={k.id} className="py-3" style={{ borderBottom: '1px solid var(--border)' }}>
                                <div className="d-flex flex-wrap align-items-center gap-2 mb-1">
                                    <Chip mono>{k.id}</Chip>
                                    <Chip tone={VERDICT_TONE[k.verdict]} className="text-uppercase">{k.verdict}</Chip>
                                    <span className="small">claim <span className="font-monospace fw-semibold">{k.target}</span></span>
                                </div>
                                <div style={{ color: 'var(--text)' }}>{k.argument}</div>
                                <div className="mt-2 d-flex flex-wrap align-items-center gap-1">
                                    <span className="small text-muted me-1">from {meta.name}’s data:</span>
                                    {k.evidence.map((id) => <CitationPopover key={id} evidenceId={id} evidence={evidence} />)}
                                </div>
                            </li>
                        ))}
                    </ul>
                </Section>
            )}

            {round3 && (
                <Section title="Round 3: final position">
                    <div className="d-flex flex-wrap align-items-center gap-2 mb-3">
                        <StanceBadge stance={round3.stance} confidence={round3.confidence} />
                        {round3.stance_change.from === round3.stance_change.to ? (
                            <span className="small text-muted">stance unchanged from round 1</span>
                        ) : (
                            <span className="small">
                                changed from <StanceBadge stance={round3.stance_change.from} /> because: {round3.stance_change.reason}
                            </span>
                        )}
                    </div>

                    {round3.changed_by.length > 0 && (
                        <div className="mb-3">
                            <div className="small fw-semibold mb-1">What changed my view</div>
                            <ul className="small mb-0 ps-3">
                                {round3.changed_by.map((v) => <li key={v.ref}><span className="font-monospace fw-semibold">{v.ref}</span>: {v.reason}</li>)}
                            </ul>
                        </div>
                    )}

                    {round3.changes.length > 0 && (
                        <div className="mb-3">
                            <div className="small fw-semibold mb-2">What happened to my round-1 claims</div>
                            <ul className="list-unstyled mb-0 d-flex flex-column gap-2">
                                {round3.changes.map((c) => (
                                    <li key={c.claim} className="d-flex flex-wrap align-items-center gap-2 small">
                                        <Chip mono>{c.claim}</Chip>
                                        <Chip tone={ACTION_TONE[c.action]} className="text-uppercase">{c.action}</Chip>
                                        <span>{c.reason}</span>
                                        {c.in_response_to.length > 0 && <span className="text-muted">(answering {c.in_response_to.join(', ')})</span>}
                                    </li>
                                ))}
                            </ul>
                        </div>
                    )}

                    <div className="small fw-semibold mb-2">
                        Final claims
                        {agent.final_verification && (
                            <span className="fw-normal text-muted"> — {agent.final_verification.verified}/{agent.final_verification.checkable} data claims verified ({pct(agent.final_verification.faithfulness, 0)})</span>
                        )}
                    </div>
                    <ClaimList claims={round3.claims} verification={agent.final_verification} evidence={evidence} />
                    <div className="mt-3"><span className="small text-muted">Risks:</span> <Risks risks={round3.risks} /></div>
                </Section>
            )}
        </div>
    );
};

export default AgentPanel;
