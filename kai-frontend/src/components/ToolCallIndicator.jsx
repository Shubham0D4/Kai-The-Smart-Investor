import React from 'react';
import { Bot, Loader2 } from 'lucide-react';
import { useCommittee } from '../context/committee';
import { AGENTS, AGENT_IDS } from '../utils/format';
import '../styles/tool-indicator.scss';

/** A floating pill while a debate runs: which agent is working and the latest step. */
const ToolCallIndicator = () => {
    const { session, debate } = useCommittee();
    if (session?.status !== 'running') return null;

    const working = AGENT_IDS.find((id) => debate?.agents?.[id]?.state === 'analyzing');
    const who = working ? AGENTS[working].name : 'Coordinator';
    const what = session.progress || 'Working…';

    return (
        <div className="tool-call-indicator-container" data-testid="tool-indicator">
            <div className="tool-pill">
                <div className="d-flex align-items-center gap-2">
                    <Bot size={18} style={{ color: 'var(--accent)' }} />
                    <div className="d-flex flex-column" style={{ maxWidth: 320 }}>
                        <span className="small fw-bold text-uppercase text-muted" style={{ fontSize: '0.65rem' }}>{who}</span>
                        <span className="fw-semibold d-flex align-items-center gap-2 text-truncate">
                            <span className="text-truncate">{what}</span>
                            <Loader2 size={12} className="spin-animation flex-shrink-0" />
                        </span>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default ToolCallIndicator;
