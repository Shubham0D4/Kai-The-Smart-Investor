import React from 'react';
import { Accordion } from 'react-bootstrap';
import { useCommittee } from '../../context/committee';
import Chip from '../../components/Chip';

/** Every LLM call the debate made, with the tokens it used (the budget is enforced on this). */
const TraceDashboard = () => {
    const { debate } = useCommittee();
    if (!debate) return null;
    const calls = debate.llm_calls || [];
    const limit = debate.token_limit;
    const used = limit ? Math.min(100, (debate.total_tokens / limit) * 100) : 0;

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
                        </div>
                        {limit ? <div className={`kai-meter${used > 80 ? ' warn' : ''}`}><span style={{ width: `${used}%` }} /></div> : null}
                    </div>
                    <div className="table-responsive custom-scrollbar" style={{ maxHeight: 300 }}>
                        <table className="kai-table">
                            <thead><tr><th className="ps-4">#</th><th>Agent</th><th>Round</th><th className="num">Tokens</th><th className="pe-4">Accepted</th></tr></thead>
                            <tbody>
                                {calls.length === 0 ? (
                                    <tr><td colSpan={5} className="text-center py-4 text-muted">No LLM calls yet.</td></tr>
                                ) : calls.map((c, i) => (
                                    <tr key={`${c.agent}-${c.round}-${i}`}>
                                        <td className="ps-4 muted">{i + 1}</td>
                                        <td className="text-capitalize">{c.agent}</td>
                                        <td>{c.round}</td>
                                        <td className="num">{c.total_tokens.toLocaleString()}</td>
                                        <td className="pe-4"><Chip tone={c.ok ? 'pos' : 'neg'}>{c.ok ? 'yes' : 'rejected'}</Chip></td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </Accordion.Body>
            </Accordion.Item>
        </Accordion>
    );
};

export default TraceDashboard;
