import React from 'react';
import { Accordion } from 'react-bootstrap';
import { useCommittee } from '../../context/committee';
import Chip from '../../components/Chip';

const n = (v) => (v || 0).toLocaleString();

/** "groq:openai/gpt-oss-120b" -> "gpt-oss-120b" (provider shown on hover). */
const shortModel = (label) => (label || '').split(':').slice(1).join(':').split('/').pop() || label;

const STATUS = {
    ok: { tone: 'pos', text: 'accepted' },
    repaired: { tone: 'warn', text: 'repaired' },
    budget: { tone: 'neutral', text: 'skipped: budget' },
    failed: { tone: 'neg', text: 'rejected' },
};

const statusOf = (c) => {
    if (c.ok) return c.repaired_from ? 'repaired' : 'ok';
    return c.error_type === 'budget' ? 'budget' : 'failed';
};

/** Each agent's share of the token limit and how much of it was used. */
const BudgetShares = ({ budget }) => (
    <div className="row g-3 mt-1" data-testid="budget-shares">
        {Object.entries(budget.agents).map(([agent, b]) => {
            const pct = b.share ? (b.used / b.share) * 100 : 0;
            return (
                <div key={agent} className="col-sm-4">
                    <div className="d-flex justify-content-between small mb-1">
                        <span className="text-capitalize" style={{ color: 'var(--text-2)' }}>{agent}</span>
                        <span className="fw-semibold">{n(b.used)} / {n(b.share)}</span>
                    </div>
                    <div className={`kai-meter${pct > 100 ? ' warn' : ''}`}><span style={{ width: `${Math.min(100, pct)}%` }} /></div>
                </div>
            );
        })}
        <div className="col-12 small text-muted">
            Shared reserve: {n(budget.reserve_left)} of {n(budget.reserve)} left. An agent that cannot afford its next step
            is left out of it with a warning, so the debate never exceeds the limit.
        </div>
    </div>
);

/** Every agent step the debate ran, which model answered, and where its tokens went. */
const TraceDashboard = () => {
    const { debate } = useCommittee();
    if (!debate) return null;
    const calls = debate.llm_calls || [];
    const limit = debate.token_limit;
    const used = limit ? Math.min(100, (debate.total_tokens / limit) * 100) : 0;
    const reasoning = calls.reduce((s, c) => s + (c.reasoning_tokens || 0), 0);

    return (
        <Accordion data-testid="trace-dashboard">
            <Accordion.Item eventKey="0" style={{ borderRadius: 'var(--radius-lg)', overflow: 'hidden' }}>
                <Accordion.Header>
                    <span className="fw-semibold">LLM calls and token budget</span>
                    <span className="ms-3 small text-muted fw-normal">{calls.length} calls</span>
                </Accordion.Header>
                <Accordion.Body className="p-0">
                    <div className="px-4 py-3">
                        <div className="small mb-2">
                            {debate.total_tokens.toLocaleString()} tokens used{limit ? ` of ${limit.toLocaleString()}` : ''}
                            {reasoning > 0 && <span className="text-muted"> · {n(reasoning)} of them hidden model reasoning</span>}
                        </div>
                        {limit ? <div className={`kai-meter${used > 80 ? ' warn' : ''}`}><span style={{ width: `${used}%` }} /></div> : null}
                        {debate.token_budget && <BudgetShares budget={debate.token_budget} />}
                    </div>
                    <div className="table-responsive custom-scrollbar" style={{ maxHeight: 340 }}>
                        <table className="kai-table" style={{ minWidth: 720 }}>
                            <thead>
                                <tr>
                                    <th className="ps-4">#</th><th>Agent</th><th>Round</th><th>Model</th>
                                    <th className="num">Prompt</th><th className="num">Output</th><th className="num">Reasoning</th>
                                    <th className="num">Total</th><th className="pe-4">Result</th>
                                </tr>
                            </thead>
                            <tbody>
                                {calls.length === 0 ? (
                                    <tr><td colSpan={9} className="text-center py-4 text-muted">No LLM calls yet.</td></tr>
                                ) : calls.map((c, i) => {
                                    const st = STATUS[statusOf(c)];
                                    const models = c.models || [];
                                    return (
                                        <tr key={`${c.agent}-${c.round}-${i}`}>
                                            <td className="ps-4 muted">{i + 1}</td>
                                            <td className="text-capitalize">{c.agent}</td>
                                            <td>{c.round}</td>
                                            <td className="small" title={models.join(' → ')}>
                                                {models.length ? models.map(shortModel).join(' → ') : <span className="muted">no call</span>}
                                            </td>
                                            <td className="num muted">{n(c.prompt_tokens)}</td>
                                            <td className="num muted">{n(c.candidates_tokens)}</td>
                                            <td className="num muted">{n(c.reasoning_tokens)}</td>
                                            <td className="num">{n(c.total_tokens)}</td>
                                            <td className="pe-4" title={c.repaired_from ? `First reply rejected: ${c.repaired_from}` : undefined}>
                                                <Chip tone={st.tone}>{st.text}</Chip>
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                </Accordion.Body>
            </Accordion.Item>
        </Accordion>
    );
};

export default TraceDashboard;
