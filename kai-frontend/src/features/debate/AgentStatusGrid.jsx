import React from 'react';
import { Activity, CalendarClock, Code, Cpu, User } from 'lucide-react';
import { useCommittee } from '../../context/committee';
import Chip from '../../components/Chip';
import { StanceBadge } from './AgentPanel';
import { AGENTS, AGENT_IDS, pct } from '../../utils/format';

const ICONS = { charlie: User, delta: Activity, gamma: Code, vega: CalendarClock, sigma: Cpu };

// label, dot colour
const STATE = {
    waiting: ['Waiting', ''],
    analyzing: ['Analyzing…', 'accent pulse'],
    judging: ['Judging…', 'accent pulse'],
    done: ['Done', 'pos'],
    failed: ['Failed', 'neg'],
    dropped: ['Dropped', 'warn'],
};

const Member = ({ id, name, role, state, tooltip, children }) => {
    const Icon = ICONS[id] || User;
    const [label, dot] = STATE[state] || STATE.waiting;
    return (
        <div className="kai-card h-100 p-3" data-testid={`member-${id}`} title={tooltip}>
            <div className="d-flex align-items-center gap-3 mb-2">
                <div className="kai-tile d-flex align-items-center justify-content-center p-0" style={{ width: 38, height: 38 }}><Icon size={18} className="text-muted" /></div>
                <div style={{ minWidth: 0 }}>
                    <div className="fw-semibold text-truncate" style={{ lineHeight: 1.25 }}>{name}</div>
                    <div className="small text-muted text-truncate">{role}</div>
                </div>
            </div>
            <div className="d-flex align-items-center gap-2 small fw-semibold" style={{ color: 'var(--text-2)' }}>
                <span className={`kai-dot ${dot}`} /> {label}
            </div>
            {children}
        </div>
    );
};

const AgentStatusGrid = () => {
    const { session, debate, sigma } = useCommittee();
    if (!session) return null;

    const running = session.status === 'running';
    let sigmaState = 'waiting';
    if (sigma) sigmaState = 'done';
    else if (session.status === 'failed') sigmaState = 'failed';
    else if (running && debate?.finished) sigmaState = 'judging';

    return (
        <section data-testid="committee">
            <div className="kai-label">Committee</div>
            <div className="row g-3">
                {AGENT_IDS.map((id) => {
                    const agent = debate?.agents?.[id];
                    const final = agent?.round3 || agent?.round1;
                    const verification = agent?.final_verification || agent?.verification;
                    return (
                        <div key={id} className="col-6 col-md-4 col-xl">
                            <Member id={id} name={AGENTS[id].name} role={AGENTS[id].role}
                                state={agent?.state || 'waiting'} tooltip={agent?.error || agent?.dropped_reason}>
                                {final && (
                                    <div className="d-flex align-items-center gap-2 mt-2 flex-wrap">
                                        <StanceBadge stance={final.stance} confidence={final.confidence} />
                                        {verification && <span className="small text-muted">{pct(verification.faithfulness, 0)} faithful</span>}
                                    </div>
                                )}
                            </Member>
                        </div>
                    );
                })}
                <div className="col-6 col-md-4 col-xl">
                    <Member id="sigma" name="Sigma" role="Judge · computes the verdict" state={sigmaState}>
                        {sigma && <div className="mt-2"><Chip tone="accent">{sigma.final_recommendation.recommendation}</Chip></div>}
                    </Member>
                </div>
            </div>
        </section>
    );
};

export default AgentStatusGrid;
