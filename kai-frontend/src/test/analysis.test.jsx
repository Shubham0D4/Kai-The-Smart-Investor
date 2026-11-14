import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, within } from '@testing-library/react';
import AnalysisPanel from '../features/debate/AnalysisPanel';
import MandateModal from '../features/mandates/MandateModal';
import { BROKEN_TEXT, fixtures, renderWithSession } from './helpers';

// The verdict card asks the backend for a live quote; the component test does not reach the network.
vi.mock('../services/api', () => ({
    getQuote: () => Promise.reject(new Error('quote not requested in component tests')),
}));

const text = (container) => container.textContent;
const SESSIONS = Object.entries(fixtures);

beforeEach(() => cleanup());

describe('every real live session renders without unhandled values', () => {
    for (const [name, session] of SESSIONS) {
        it(name, () => {
            const { container } = renderWithSession(<AnalysisPanel />, session);
            expect(text(container)).not.toMatch(BROKEN_TEXT);
            expect(screen.getByTestId('analysis-panel')).toBeTruthy();
        });
    }
});

describe.each(SESSIONS)('verdict card: %s', (name, session) => {
    const rec = session.sigma.final_recommendation;

    it("shows Sigma's computed verdict with its confidence (Hold included), gates and explanation", () => {
        renderWithSession(<AnalysisPanel />, session);
        expect(screen.getByTestId('verdict').textContent).toContain(rec.recommendation);
        const conf = screen.getByTestId('confidence').textContent;
        expect(conf).toContain(`${Math.round(rec.confidence)}%`);
        if (rec.recommendation === 'Hold') expect(conf).toContain('confidence in Hold');
        const card = screen.getByTestId('sigma-card');
        for (const gate of rec.gates) expect(card.textContent).toContain(gate.note);
        expect(card.textContent).toContain(rec.analysis);
        expect(card.textContent).toContain('cannot change the verdict');
    });

    it('shows the market snapshot every agent worked from', () => {
        renderWithSession(<AnalysisPanel />, session);
        const snap = screen.getByTestId('snapshot').textContent;
        expect(snap).toContain(session.debate.market_snapshot.market_date);
        expect(snap).toContain(session.debate.market_snapshot.fetched_at.slice(0, 10));
    });

    it('lists every computed suggestion with its detail', () => {
        renderWithSession(<AnalysisPanel />, session);
        const playbook = screen.getByTestId('playbook');
        expect(rec.suggestions.length).toBeGreaterThan(2);
        for (const s of rec.suggestions) {
            expect(playbook.textContent).toContain(s.title);
            expect(playbook.textContent).toContain(s.detail);
        }
    });

    it("lists each persona's verdict for the same debate", () => {
        renderWithSession(<AnalysisPanel />, session);
        const card = screen.getByTestId('sigma-card');
        for (const p of ['zen', 'balanced', 'alpha']) expect(card.textContent.toLowerCase()).toContain(p);
    });
});

describe.each(SESSIONS)('agents and committee: %s', (name, session) => {
    it('renders all four agents: stance, claims with their evidence ids, and verification marks', () => {
        const { container } = renderWithSession(<AnalysisPanel />, session);
        expect(Object.keys(session.debate.agents)).toEqual(['charlie', 'delta', 'gamma', 'vega']);
        for (const [id, agent] of Object.entries(session.debate.agents)) {
            if (!agent.round1) continue;
            const panel = screen.getByTestId(`agent-panel-${id}`);
            expect(panel.textContent.toUpperCase()).toContain(agent.round1.stance.toUpperCase());
            for (const claim of agent.round1.claims) {
                expect(panel.textContent).toContain(claim.text);
                for (const ev of claim.evidence || []) expect(panel.textContent).toContain(ev);
            }
        }
        expect(container.querySelectorAll('.kai-evidence').length).toBeGreaterThan(10);
    });

    it("shows each committee member in the state the debate recorded", () => {
        renderWithSession(<AnalysisPanel />, session);
        const label = { done: 'Done', failed: 'Failed', dropped: 'Dropped' };
        for (const [id, agent] of Object.entries(session.debate.agents)) {
            expect(screen.getByTestId(`member-${id}`).textContent).toContain(label[agent.state]);
        }
        expect(screen.getByTestId('member-sigma').textContent).toContain('Done');
    });

    it('shows the LLM calls, each agent budget share, and which model answered each step', () => {
        renderWithSession(<AnalysisPanel />, session);
        const trace = screen.getByTestId('trace-dashboard');
        expect(trace.textContent).toContain(`${session.debate.total_tokens.toLocaleString()} tokens used`);
        expect(trace.textContent).toContain(`${session.debate.llm_calls.length} calls`);
        const shares = screen.getByTestId('budget-shares');
        for (const [agent, b] of Object.entries(session.debate.token_budget.agents)) {
            expect(shares.textContent.toLowerCase()).toContain(agent);
            expect(shares.textContent).toContain(`${b.used.toLocaleString()} / ${b.share.toLocaleString()}`);
        }
        const first = session.debate.llm_calls[0];
        expect(trace.textContent).toContain(first.prompt_tokens.toLocaleString());
        expect(trace.textContent).toContain(first.models[0].split('/').pop());
    });

    it('shows the persisted log entries, not invented ones', () => {
        renderWithSession(<AnalysisPanel />, session);
        const feed = screen.getByTestId('debate-feed');
        expect(feed.textContent).toContain(`${session.debate.events.length} entries`);
        expect(feed.textContent).toContain(session.debate.events[0].message);
    });

    it('shows every quant field with its sample size, or "unavailable" with the reason', () => {
        renderWithSession(<AnalysisPanel />, session);
        const report = session.debate.quant_report;
        expect(screen.getByTestId('quant-panel').textContent).toContain(`seed ${report.seed}`);
        for (const [id, field] of Object.entries(report.fields)) {
            const row = screen.queryByTestId(`quant-${id}`);
            if (!row) continue;
            if (field.status === 'ok') expect(row.textContent).toContain(field.n_kind);
            else expect(row.textContent).toContain(`unavailable: ${field.reason.slice(0, 30)}`);
        }
    });

    it('lists the contradictions with the quant report when there are any', () => {
        renderWithSession(<AnalysisPanel />, session);
        const list = session.sigma.final_recommendation.contradictions;
        const box = screen.queryByTestId('contradictions');
        if (list.length === 0) expect(box).toBeNull();
        else expect(box.querySelectorAll('li').length).toBe(list.length);
    });
});

describe('agents the fact-check removed or that failed (TSLA live run)', () => {
    const session = fixtures.tslaAlpha;

    it('shows the dropped agent and why, and the failed claim with the value the data gives', () => {
        renderWithSession(<AnalysisPanel />, session);
        const gamma = session.debate.agents.gamma;
        expect(gamma.state).toBe('dropped');
        expect(screen.getByTestId('member-gamma').textContent).toContain('Dropped');
        expect(screen.getByTestId('agent-panel-gamma').textContent).toContain('dropped by the circuit breaker');
        const charlie = screen.getByTestId('agent-panel-charlie').textContent;
        expect(charlie).toContain('failed: value_mismatch');
        expect(charlie).toMatch(/the data gives/);
    });

    it('names the agents left out of the verdict', () => {
        renderWithSession(<AnalysisPanel />, session);
        const warn = screen.getByTestId('verdict-warnings').textContent;
        for (const a of session.sigma.final_recommendation.agents_missing) expect(warn).toContain(a);
    });
});

describe('session states using real data', () => {
    it('a stored session id whose data has not arrived yet shows loading, then a poll error with Retry', () => {
        const { value } = renderWithSession(<AnalysisPanel />, null, { sessionId: 'sess_x', pollError: 'Cannot reach the Kai backend. Is it running?' });
        expect(screen.getByTestId('banner-loading').textContent).toContain('Cannot reach the Kai backend');
        expect(value.sessionId).toBe('sess_x');
    });

    it("a session for another ticker is hidden on that ticker's page", () => {
        const { container } = renderWithSession(<AnalysisPanel symbol="MSFT" />, fixtures.aaplZen);
        expect(container.textContent).toBe('');
    });

    it('lost contact keeps the last data visible and offers Retry', () => {
        const { value } = renderWithSession(<AnalysisPanel />, fixtures.aaplZen, { pollError: 'Cannot reach the Kai backend. Is it running?' });
        const banner = screen.getByTestId('banner-poll-error');
        expect(banner.textContent).toContain('Lost contact');
        fireEvent.click(within(banner).getByText('Retry'));
        expect(value.retryPolling).toHaveBeenCalledTimes(1);
        expect(screen.getByTestId('sigma-card')).toBeTruthy();
    });

    it('does not render an approval modal for a Hold (nothing to approve)', () => {
        renderWithSession(<MandateModal />, fixtures.nvdaBalanced);
        expect(document.body.querySelector('.modal')).toBeNull();
    });
});
