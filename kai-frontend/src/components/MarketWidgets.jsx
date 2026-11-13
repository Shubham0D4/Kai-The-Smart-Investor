import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useCommittee } from '../context/committee';
import usePolled from '../hooks/usePolled';
import { getAnalysisHistory, getIndices, getQuote } from '../services/api';
import { dateTime } from '../utils/format';
import Chip from './Chip';

const WATCHLIST = [
    { symbol: 'NVDA', name: 'NVIDIA' }, { symbol: 'AAPL', name: 'Apple' }, { symbol: 'TSLA', name: 'Tesla' },
    { symbol: 'AMD', name: 'AMD' }, { symbol: 'MSFT', name: 'Microsoft' }, { symbol: 'GOOGL', name: 'Alphabet' },
    { symbol: 'AMZN', name: 'Amazon' }, { symbol: 'BTC-USD', name: 'Bitcoin' }, { symbol: 'ETH-USD', name: 'Ethereum' },
];

const fetchWatchlist = () => Promise.all(WATCHLIST.map(async (item) => {
    try {
        return { ...item, quote: await getQuote(item.symbol) };
    } catch (error) {
        return { ...item, error: error.message };
    }
}));

const Panel = ({ title, subtitle, children }) => (
    <section className="kai-card h-100">
        <div className="kai-card-head">
            <div>
                <h2 className="kai-card-title">{title}</h2>
                {subtitle && <div className="kai-card-sub">{subtitle}</div>}
            </div>
        </div>
        <div className="p-2">{children}</div>
    </section>
);

const Note = ({ children }) => <div className="small text-muted px-3 py-2" role="status">{children}</div>;
const Unavailable = ({ error, what }) => <Note>{what} unavailable{error ? `: ${error}` : ''}</Note>;

const Change = ({ value, positive }) => (
    <span className={`small fw-semibold ${positive ? 'pos-text' : 'neg-text'}`}>{value}</span>
);

export const WatchlistWidget = () => {
    const navigate = useNavigate();
    const { data, error, loading } = usePolled(fetchWatchlist, 60000);
    return (
        <Panel title="Watchlist" subtitle="Click a symbol for its chart and analysis">
            {loading && <Note>Loading…</Note>}
            {error && !data && <Unavailable error={error} what="Watchlist" />}
            {(data || []).map((item) => (
                <button key={item.symbol} type="button" className="kai-listbtn" onClick={() => navigate(`/ticker/${item.symbol}`)}>
                    <span>
                        <span className="fw-semibold d-block" style={{ lineHeight: 1.25 }}>{item.symbol}</span>
                        <span className="small text-muted">{item.name}</span>
                    </span>
                    {item.quote ? (
                        <span className="text-end">
                            <span className="fw-semibold d-block" style={{ lineHeight: 1.25 }}>${item.quote.price}</span>
                            <Change value={item.quote.change} positive={item.quote.is_positive} />
                        </span>
                    ) : <span className="small text-muted" title={item.error}>unavailable</span>}
                </button>
            ))}
        </Panel>
    );
};

/** Compact strip of the major indices, one tile each. */
export const IndicesWidget = () => {
    const { data, error, loading } = usePolled(getIndices, 60000);
    return (
        <section className="kai-card" aria-label="Major indices" data-testid="market-strip">
            {loading && <Note>Loading markets…</Note>}
            {error && <Unavailable error={error} what="Market indices" />}
            <div className="kai-strip">
                {(data || []).map((idx) => (
                    <div key={idx.name} className="kai-strip-item">
                        <div className="kai-label mb-1">{idx.name}</div>
                        {idx.available ? (
                            <div className="d-flex align-items-baseline gap-2 flex-wrap">
                                <span style={{ fontSize: '1.25rem', fontWeight: 650, letterSpacing: '-0.01em' }}>{idx.value}</span>
                                <Change value={idx.change} positive={idx.positive} />
                            </div>
                        ) : <span className="small text-muted" title={idx.error}>unavailable</span>}
                    </div>
                ))}
            </div>
        </section>
    );
};

/** Real headlines from the backend's news providers (`fetcher` returns { articles }). */
export const NewsWidget = ({ title, fetcher }) => {
    const { data, error, loading } = usePolled(fetcher, 300000);
    return (
        <Panel title={title} subtitle="Merged from several free news feeds">
            {loading && <Note>Loading…</Note>}
            {error && !data && <Unavailable error={error} what="News" />}
            {data && data.articles.length === 0 && <Note>No recent articles.</Note>}
            {(data?.articles || []).map((a) => (
                <div key={a.id} className="px-3 py-2">
                    {a.url
                        ? <a href={a.url} target="_blank" rel="noreferrer" className="fw-semibold d-block" style={{ color: 'var(--text)', lineHeight: 1.35 }}>{a.title}</a>
                        : <span className="fw-semibold d-block" style={{ lineHeight: 1.35 }}>{a.title}</span>}
                    <span className="small text-muted">{a.source || 'Unknown source'}{a.published_at ? ` · ${dateTime(a.published_at)}` : ''}</span>
                </div>
            ))}
            {data && Object.keys(data.providers_failed || {}).length > 0 && (
                <Note>Some news sources failed: {Object.keys(data.providers_failed).join(', ')}</Note>
            )}
        </Panel>
    );
};

const STATUS_TONE = { completed: 'plain', waiting_approval: 'warn', rejected: 'neutral', failed: 'neg', running: 'info' };
const REC_TONE = { Buy: 'pos', Sell: 'neg', Hold: 'neutral', 'No Verdict': 'warn' };
const STATUS_LABEL = { waiting_approval: 'needs approval', failed: 'failed', running: 'running', rejected: 'rejected' };

/** Past analyses stored by the backend; click one to open it. Refreshes when the active session changes. */
export const HistoryWidget = () => {
    const { session, sessionId, loadSession } = useCommittee();
    const { data, error } = usePolled(getAnalysisHistory, 0, `${sessionId}:${session?.status}`);
    return (
        <Panel title="Recent analyses" subtitle="Stored locally; click to reopen">
            {error && !data && <Unavailable error={error} what="History" />}
            {data && data.analyses.length === 0 && <Note>No analyses yet. Run one above.</Note>}
            {(data?.analyses || []).slice(0, 7).map((h) => (
                <button key={h.session_id} type="button" className="kai-listbtn" onClick={() => loadSession(h.session_id)} title={h.error || dateTime(h.created_at)}>
                    <span>
                        <span className="fw-semibold d-block" style={{ lineHeight: 1.25 }}>{h.ticker}</span>
                        <span className="small text-muted text-capitalize">{h.persona} · {dateTime(h.created_at)}</span>
                    </span>
                    <span className="d-flex gap-1 flex-shrink-0">
                        {h.recommendation && <Chip tone={REC_TONE[h.recommendation] || 'neutral'}>{h.recommendation}</Chip>}
                        {(!h.recommendation || h.status !== 'completed') && (
                            <Chip tone={STATUS_TONE[h.status] || 'neutral'}>{STATUS_LABEL[h.status] || h.status}</Chip>
                        )}
                    </span>
                </button>
            ))}
        </Panel>
    );
};
