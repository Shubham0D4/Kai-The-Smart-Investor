import React, { useCallback, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import SearchInterface from '../components/SearchInterface';
import { NewsWidget } from '../components/MarketWidgets';
import AnalysisPanel from '../features/debate/AnalysisPanel';
import usePolled from '../hooks/usePolled';
import { useMarketSocket } from '../hooks/useMarketSocket';
import { usePriceGlow } from '../hooks/usePriceGlow';
import { getPriceHistory, getQuote, getTickerNews } from '../services/api';
import { DASH, compact } from '../utils/format';

const PERIODS = ['1d', '5d', '1mo', 'ytd', '1y'];

const Stat = ({ label, value }) => (
    <div className="d-flex justify-content-between py-2" style={{ borderBottom: '1px solid var(--border)' }}>
        <span className="text-muted">{label}</span><span className="fw-semibold">{value}</span>
    </div>
);

const TickerPage = () => {
    const { symbol } = useParams();
    const [period, setPeriod] = useState('1d');

    const quoteFetcher = useCallback(() => getQuote(symbol), [symbol]);
    const historyFetcher = useCallback(() => getPriceHistory(symbol, period), [symbol, period]);
    const newsFetcher = useCallback(() => getTickerNews(symbol), [symbol]);
    const { data: quote, error: quoteError } = usePolled(quoteFetcher, 60000);
    const { data: chartData, error: chartError } = usePolled(historyFetcher, 60000);

    const socket = useMarketSocket(symbol);                 // live trades when VITE_FINNHUB_API_KEY is set
    const displayPrice = socket?.price ?? (quote ? Number(String(quote.price).replace(/,/g, '')) : null);
    const glowClass = usePriceGlow(displayPrice);

    return (
        <div className="kai-stack">
            <div className="d-flex align-items-end justify-content-between flex-wrap gap-3">
                <div>
                    <div className="kai-label mb-1">{quote?.name || 'Ticker'}</div>
                    <div className="d-flex align-items-baseline gap-3 flex-wrap">
                        <h1 className="mb-0" style={{ fontSize: '2rem' }}>{symbol}</h1>
                        {displayPrice !== null && Number.isFinite(displayPrice) && (
                            <span className={`${glowClass}`} style={{ fontSize: '1.5rem', fontWeight: 650 }}>
                                ${displayPrice.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                            </span>
                        )}
                        {quote && <span className={`fw-semibold ${quote.is_positive ? 'pos-text' : 'neg-text'}`}>{quote.change}</span>}
                    </div>
                </div>
                <Link to="/" className="small">&larr; Back to dashboard</Link>
            </div>
            {quoteError && !quote && <div className="kai-alert neg" role="alert" data-testid="quote-error">Quote unavailable: {quoteError}</div>}

            <div className="row g-4">
                <div className="col-lg-8">
                    <section className="kai-card h-100">
                        <div className="kai-card-head">
                            <h2 className="kai-card-title">Price</h2>
                            <div className="kai-segment" role="group" aria-label="Period">
                                {PERIODS.map((p) => (
                                    <button key={p} type="button" aria-pressed={period === p} onClick={() => setPeriod(p)}>{p.toUpperCase()}</button>
                                ))}
                            </div>
                        </div>
                        <div className="kai-card-body">
                            {chartError && !chartData && <div className="kai-alert warn" data-testid="chart-error">Price history unavailable: {chartError}</div>}
                            {chartData && (
                                <ResponsiveContainer width="100%" height={320}>
                                    <AreaChart data={chartData}>
                                        <defs>
                                            <linearGradient id="colorPrice" x1="0" y1="0" x2="0" y2="1">
                                                <stop offset="5%" stopColor="var(--accent)" stopOpacity={0.22} />
                                                <stop offset="95%" stopColor="var(--accent)" stopOpacity={0} />
                                            </linearGradient>
                                        </defs>
                                        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border)" />
                                        <XAxis dataKey="name" axisLine={false} tickLine={false} tick={{ fill: 'var(--text-3)', fontSize: 12 }} dy={10} minTickGap={30} />
                                        <YAxis domain={['auto', 'auto']} axisLine={false} tickLine={false} tick={{ fill: 'var(--text-3)', fontSize: 12 }} dx={-6} width={52} />
                                        <Tooltip contentStyle={{ borderRadius: 10, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', boxShadow: 'var(--shadow-md)' }} />
                                        <Area type="monotone" dataKey="price" stroke="var(--accent)" strokeWidth={2} fillOpacity={1} fill="url(#colorPrice)" />
                                    </AreaChart>
                                </ResponsiveContainer>
                            )}
                        </div>
                    </section>
                </div>
                <div className="col-lg-4">
                    <section className="kai-card h-100">
                        <div className="kai-card-head"><h2 className="kai-card-title">Key statistics</h2></div>
                        <div className="kai-card-body py-2">
                            <Stat label="Market cap" value={quote?.market_cap ? compact(quote.market_cap) : DASH} />
                            <Stat label="P/E ratio" value={quote?.pe_ratio ? quote.pe_ratio.toFixed(2) : DASH} />
                            <Stat label="Dividend yield" value={quote?.dividend_yield || DASH} />
                            <Stat label="52-week range" value={quote?.low_52w && quote?.high_52w ? `${quote.low_52w} – ${quote.high_52w}` : DASH} />
                        </div>
                    </section>
                </div>
            </div>

            <SearchInterface initialTicker={symbol} />
            <AnalysisPanel symbol={symbol} />

            <div className="row g-4">
                <div className="col-lg-6"><NewsWidget title={`${symbol} news`} fetcher={newsFetcher} /></div>
            </div>
        </div>
    );
};

export default TickerPage;
