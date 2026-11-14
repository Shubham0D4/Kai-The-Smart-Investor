import React from 'react';
import { Tab, Tabs } from 'react-bootstrap';
import { useCommittee } from '../../context/committee';
import { AGENTS, AGENT_IDS } from '../../utils/format';
import AgentPanel from './AgentPanel';
import AgentStatusGrid from './AgentStatusGrid';
import DebateFeed from './DebateFeed';
import QuantPanel from './QuantPanel';
import SessionBanner from './SessionBanner';
import SigmaCard from './SigmaCard';
import TraceDashboard from './TraceDashboard';

/** Price the quant report's fair-value range is compared with: the debate's market snapshot. */
const reportPrice = (debate) => debate?.market_snapshot?.price
    ?? debate?.agents?.gamma?.evidence?.find((e) => e.id === 'mkt:price')?.value;

/**
 * Everything about the active session, top to bottom: state, committee, verdict, quant report,
 * per-agent detail (stance, claims with evidence, round-3 changes), log and token trace.
 * `symbol` (optional) hides a session that belongs to a different ticker.
 */
const AnalysisPanel = ({ symbol }) => {
    const { sessionId, session, debate, sigma } = useCommittee();
    if (!sessionId) return null;
    if (session && symbol && session.ticker !== symbol.toUpperCase()) return null;

    const hasAgentDetail = debate && AGENT_IDS.some((id) => {
        const a = debate.agents[id];
        return a.round1 || a.error || a.dropped_reason;
    });

    return (
        <div className="kai-stack" data-testid="analysis-panel">
            <SessionBanner />
            {session && <AgentStatusGrid />}
            {sigma && <SigmaCard />}
            {debate?.quant_report && <QuantPanel report={debate.quant_report} price={reportPrice(debate)} />}
            {hasAgentDetail && (
                <section className="kai-card" data-testid="agent-details">
                    <div className="kai-card-head">
                        <div>
                            <h2 className="kai-card-title">What each agent said</h2>
                            <div className="kai-card-sub">Claims, the evidence behind them, how they were challenged, and what changed</div>
                        </div>
                    </div>
                    <div className="px-4 pb-4 pt-3">
                        <Tabs defaultActiveKey={AGENT_IDS[0]}>
                            {AGENT_IDS.map((id) => (
                                <Tab key={id} eventKey={id} title={AGENTS[id].name}>
                                    <AgentPanel agent={debate.agents[id]} />
                                </Tab>
                            ))}
                        </Tabs>
                    </div>
                </section>
            )}
            <DebateFeed />
            <TraceDashboard />
        </div>
    );
};

export default AnalysisPanel;
