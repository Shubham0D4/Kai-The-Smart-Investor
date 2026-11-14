import React from 'react';
import { FileSearch, Gavel, Users } from 'lucide-react';
import SearchInterface from '../components/SearchInterface';
import { HistoryWidget, IndicesWidget, NewsWidget, WatchlistWidget } from '../components/MarketWidgets';
import AnalysisPanel from '../features/debate/AnalysisPanel';
import { useCommittee } from '../context/committee';
import { getMarketNews } from '../services/api';

const STEPS = [
    { icon: FileSearch, title: '1 · Evidence is fetched', text: 'SEC financial statements, news from several feeds, prices and market history are fetched once, in code, before any model runs.' },
    { icon: Users, title: '2 · Agents debate, then are fact-checked', text: 'Charlie (fundamentals), Delta (news), Gamma (valuation) and Vega (catalysts & options) each argue only their own domain, from one market snapshot. Every number is re-checked against the data; agents that fail are dropped.' },
    { icon: Gavel, title: '3 · The verdict is computed', text: 'The judge weighs the four domains, applies fixed Buy/Sell rules and a quantitative report, and lists what would change the verdict. The language model only explains the result; it cannot change it.' },
];

const HowItWorks = () => (
    <section className="kai-card" data-testid="how-it-works">
        <div className="kai-card-body">
            <div className="kai-label">How it works</div>
            <div className="kai-steps">
                {STEPS.map(({ icon: Icon, title, text }) => (
                    <div key={title}>
                        <div className="d-flex align-items-center gap-2 mb-2"><Icon size={18} className="text-muted" /><h3 className="mb-0 fs-6">{title}</h3></div>
                        <p className="small mb-0">{text}</p>
                    </div>
                ))}
            </div>
        </div>
    </section>
);

const Dashboard = () => {
    const { sessionId } = useCommittee();
    return (
        <div className="kai-stack">
            <IndicesWidget />
            <SearchInterface />

            {sessionId ? <AnalysisPanel /> : <HowItWorks />}

            <div className="row g-4">
                <div className="col-lg-4"><WatchlistWidget /></div>
                <div className="col-lg-4"><HistoryWidget /></div>
                <div className="col-lg-4"><NewsWidget title="Market news" fetcher={getMarketNews} /></div>
            </div>
        </div>
    );
};

export default Dashboard;
