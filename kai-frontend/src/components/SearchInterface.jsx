import React, { useEffect, useRef, useState } from 'react';
import { Button, Form, Spinner } from 'react-bootstrap';
import { Scale, Search, Shield, Zap } from 'lucide-react';
import { useCommittee } from '../context/committee';

const PERSONAS = [
    { id: 'Zen', icon: Shield, hint: 'Strictest thresholds: Buy needs a wide margin of safety and low volatility.' },
    { id: 'Balanced', icon: Scale, hint: 'Middle thresholds.' },
    { id: 'Alpha', icon: Zap, hint: 'Most permissive thresholds: willing to Buy without a margin of safety.' },
];

const SearchInterface = ({ initialTicker = '' }) => {
    const { session, starting, startError, startAnalysis } = useCommittee();
    const [ticker, setTicker] = useState(initialTicker);
    const [persona, setPersona] = useState('Balanced');
    const inputRef = useRef(null);
    const running = session?.status === 'running';

    // Global shortcut: Cmd/Ctrl+K focuses the ticker box
    useEffect(() => {
        const onKeyDown = (e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
                e.preventDefault();
                inputRef.current?.focus();
            }
        };
        window.addEventListener('keydown', onKeyDown);
        return () => window.removeEventListener('keydown', onKeyDown);
    }, []);

    const handleSubmit = (e) => {
        e.preventDefault();
        if (!ticker.trim() || running) return;
        startAnalysis(ticker, persona);
    };

    return (
        <section className="kai-card" data-testid="search-card">
            <div className="kai-card-body">
                <h1 className="mb-1">Analyze a ticker</h1>
                <p className="mb-4" style={{ maxWidth: 620 }}>
                    Three specialist agents read the SEC filings, the news and the market data, challenge each other, and a
                    judge computes the verdict. Every figure they cite is checked against the fetched data.
                </p>

                <Form onSubmit={handleSubmit}>
                    <div className="d-flex flex-column flex-md-row gap-3 align-items-stretch align-items-md-center">
                        <div className="position-relative flex-grow-1">
                            <Search size={18} className="position-absolute text-muted" style={{ left: 14, top: '50%', transform: 'translateY(-50%)' }} />
                            <Form.Control
                                ref={inputRef}
                                aria-label="Ticker symbol"
                                placeholder="Ticker, e.g. AAPL"
                                style={{ paddingLeft: 42, height: 46, fontWeight: 600, letterSpacing: '0.02em', minWidth: 220 }}
                                value={ticker}
                                maxLength={10}
                                onChange={(e) => setTicker(e.target.value.toUpperCase())}
                            />
                        </div>
                        <div className="kai-segment" role="group" aria-label="Decision thresholds">
                            {PERSONAS.map(({ id, icon: Icon, hint }) => (
                                <button key={id} type="button" aria-pressed={persona === id} onClick={() => setPersona(id)} title={hint}>
                                    <Icon size={15} />{id}
                                </button>
                            ))}
                        </div>
                        <Button variant="primary" type="submit" disabled={starting || running || !ticker.trim()} style={{ height: 46, minWidth: 170 }}>
                            {starting || running ? (
                                <span className="d-inline-flex align-items-center gap-2">
                                    <Spinner as="span" animation="border" size="sm" aria-hidden="true" />
                                    {starting ? 'Starting…' : 'Debate in progress…'}
                                </span>
                            ) : 'Run analysis'}
                        </Button>
                    </div>
                    <p className="small text-muted mt-3 mb-0">
                        The thresholds setting does not change how the agents analyse; it only sets how strict the final Buy/Sell rules are. Press Ctrl/Cmd + K to focus the ticker box.
                    </p>
                </Form>

                {startError && (
                    <div className="kai-alert neg mt-3" role="alert" data-testid="start-error">{startError}</div>
                )}
            </div>
        </section>
    );
};

export default SearchInterface;
