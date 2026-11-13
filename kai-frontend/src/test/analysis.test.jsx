import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, within } from '@testing-library/react';
import AnalysisPanel from '../features/debate/AnalysisPanel';
import MandateModal from '../features/mandates/MandateModal';
import ToolCallIndicator from '../components/ToolCallIndicator';
import { BROKEN_TEXT, fixtures, renderWithSession } from './helpers';

// A plain function (not vi.fn): restoreMocks would otherwise wipe its implementation between tests.
vi.mock('../services/api', () => ({
    getQuote: () => Promise.resolve({ price: '354.88', change: '-0.17%', is_positive: false }),
}));

const text = (container) => container.textContent;

beforeEach(() => cleanup());

describe('every real scenario renders without unhandled values', () => {
    for (const [name, session] of Object.entries(fixtures)) {
        it(name, () => {
            const { container } = renderWithSession(<AnalysisPanel />, session);
            expect(text(container)).not.toMatch(BROKEN_TEXT);
            expect(screen.getByTestId('analysis-panel')).toBeTruthy();
        });
    }
});

describe('completed Hold', () => {
    const session = fixtures.holdCompleted;

    it("shows Sigma's computed verdict, no confidence for a Hold, its gates and the explanation", () => {
        renderWithSession(<AnalysisPanel />, session);
        expect(screen.getByTestId('verdict').textContent).toContain('Hold');
        expect(screen.getByTestId('confidence').textContent).toMatch(/No confidence/);
        const card = screen.getByTestId('sigma-card');
        for (const gate of session.sigma.final_recommendation.gates) {
            expect(card.textContent).toContain(gate.note);
        }
        expect(card.textContent).toContain(session.sigma.final_recommendation.analysis);
        expect(card.textContent).toContain('cannot change the verdict');
        expect(within(card).getByText(/Signed alpha packet/)).toBeTruthy();
    });

    it('lists each persona\'s verdict for the same debate', () => {
        renderWithSession(<AnalysisPanel />, session);
        const card = screen.getByTestId('sigma-card');
        for (const p of ['zen', 'balanced', 'alpha']) expect(card.textContent.toLowerCase()).toContain(p);
    });

    it('renders every agent: stance, claims with their evidence ids, and verification marks', () => {
        const { container } = renderWithSession(<AnalysisPanel />, session);
        for (const [id, agent] of Object.entries(session.debate.agents)) {
            const panel = screen.getByTestId(`agent-panel-${id}`);
            expect(panel.textContent.toUpperCase()).toContain(agent.round1.stance.toUpperCase());
            for (const claim of agent.round1.claims) {
                expect(panel.textContent).toContain(claim.text);
                for (const ev of claim.evidence || []) expect(panel.textContent).toContain(ev);
            }
            expect(panel.textContent).toContain('verified');
            expect(panel.textContent).toContain('Round 2: challenges and confirmations');
            expect(panel.textContent).toContain('Round 3: final position');
        }
        expect(container.querySelectorAll('.kai-evidence').length).toBeGreaterThan(10);
    });

    it('shows what changed in round 3: every critique answered and the stance change', () => {
        renderWithSession(<AnalysisPanel />, session);
        const charlie = screen.getByTestId('agent-panel-charlie');
        const r3 = session.debate.agents.charlie.round3;
        expect(charlie.textContent).toContain('What happened to my round-1 claims');
        for (const change of r3.changes) expect(charlie.textContent).toContain(change.action);
        expect(charlie.textContent).toContain('stance unchanged from round 1');
    });

    it('shows the committee with each agent done and Sigma done', () => {
        renderWithSession(<AnalysisPanel />, session);
        for (const id of ['charlie', 'delta', 'gamma', 'sigma']) {
            expect(screen.getByTestId(`member-${id}`).textContent).toContain('Done');
        }
    });

    it('shows the LLM calls and token budget actually used', () => {
        renderWithSession(<AnalysisPanel />, session);
        const trace = screen.getByTestId('trace-dashboard');
        expect(trace.textContent).toContain(`${session.debate.total_tokens.toLocaleString()} tokens used`);
        expect(trace.textContent).toContain(`${session.debate.llm_calls.length} calls`);
    });

    it('shows the persisted log entries, not invented ones', () => {
        renderWithSession(<AnalysisPanel />, session);
        const feed = screen.getByTestId('debate-feed');
        expect(feed.textContent).toContain(`${session.debate.events.length} entries`);
        expect(feed.textContent).toContain(session.debate.events[0].message);
    });
});

describe('quant report', () => {
    const session = fixtures.buyContradiction;
    const report = session.debate.quant_report;

    it('shows ranges and probabilities with their sample sizes, kinds and the seed', () => {
        renderWithSession(<AnalysisPanel />, session);
        const panel = screen.getByTestId('quant-panel');
        expect(panel.textContent).toContain(`seed ${report.seed}`);
        expect(panel.textContent).toContain('past trend removed');                 // drift_mode zero
        for (const id of [
            'quant:forward.prob_up_bootstrap', 'quant:mc_dcf.prob_fair_above_price',
            'quant:mc_dcf.fair_value_p10', 'quant:market.beta', 'quant:returns.vol_annual',
        ]) {
            const field = report.fields[id];
            const row = screen.getByTestId(`quant-${id}`);
            expect(row.textContent).toContain(`${field.n.toLocaleString()} ${field.n_kind}`);
        }
        const forward = screen.getByTestId('quant-quant:forward.prob_up_bootstrap').textContent;
        expect(forward).toMatch(/\d+\.\d%/);                                           // the probability
        expect(forward).toContain('90%');                                              // with its interval level
        expect(panel.textContent).toContain('describes the past');
        expect(panel.textContent).toContain('prediction');
        expect(panel.querySelector('[aria-label="Fair value range against current price"]')).toBeTruthy();
    });

    it('says "unavailable" with the reason instead of a number', () => {
        renderWithSession(<AnalysisPanel />, session);
        const base = screen.getByTestId('quant-quant:base_rate.share_up');
        expect(base.textContent).toContain('unavailable: insufficient data');
        expect(base.textContent).not.toMatch(/\d+\.\d%/);
        expect(screen.getByTestId('quant-quant:calibration.brier').textContent).toContain('unavailable');
    });

    it('flags agents that contradict the report and shows how confidence was reduced', () => {
        renderWithSession(<AnalysisPanel />, session);
        const box = screen.getByTestId('contradictions');
        expect(box.textContent).toContain('Contradicts the quant report');
        expect(box.querySelectorAll('li').length).toBe(session.sigma.final_recommendation.contradictions.length);
        const rec = session.sigma.final_recommendation;
        const card = screen.getByTestId('sigma-card');
        expect(rec.confidence).toBeLessThan(rec.confidence_before_quant);
        expect(card.textContent).toContain(`${Math.round(rec.confidence_before_quant)}% before the quant report`);
        expect(screen.getByTestId('confidence').textContent).toContain(`${Math.round(rec.confidence)}% confidence`);
    });
});

describe('circuit breaker', () => {
    const session = fixtures.circuitBreaker;

    it('explains why there is no verdict and which agents were dropped', () => {
        renderWithSession(<AnalysisPanel />, session);
        const banner = screen.getByTestId('banner-no-verdict');
        expect(banner.textContent).toContain('Circuit breaker');
        expect(banner.textContent).toContain(session.debate.outcome.detail);
        expect(banner.textContent).toContain('Charlie dropped');
        expect(banner.textContent).toContain('Delta dropped');
        expect(banner.textContent).toContain('none is guessed');
        expect(screen.getByTestId('verdict').textContent).toContain('No Verdict');
        expect(screen.getByTestId('member-charlie').textContent).toContain('Dropped');
        expect(screen.getByTestId('agent-panel-charlie').textContent).toContain('dropped by the circuit breaker');
    });

    it('shows the failed claims with the data value they contradict', () => {
        renderWithSession(<AnalysisPanel />, session);
        const panel = screen.getByTestId('agent-panel-charlie');
        expect(panel.textContent).toContain('failed: value_mismatch');
        expect(panel.textContent).toMatch(/the data gives/);
    });
});

describe('running, failed and loading states', () => {
    it('running: shows progress, the working agent and no verdict yet', () => {
        const session = fixtures.running;
        const { container } = renderWithSession(
            <><AnalysisPanel /><ToolCallIndicator /></>, session);
        expect(screen.getByTestId('banner-running').textContent).toContain('round 1 of 3');
        expect(screen.getByTestId('member-charlie').textContent).toContain('Done');
        expect(screen.getByTestId('member-delta').textContent).toContain('Analyzing');
        expect(screen.getByTestId('member-gamma').textContent).toContain('Waiting');
        expect(screen.queryByTestId('sigma-card')).toBeNull();
        expect(screen.getByTestId('quant-panel')).toBeTruthy();                      // built before round 1
        expect(screen.getByTestId('tool-indicator').textContent).toContain('Delta');
        expect(text(container)).toContain(session.progress.slice(0, 20));
    });

    it('failed: shows the server\'s error and nothing else', () => {
        renderWithSession(<AnalysisPanel />, fixtures.failed);
        const banner = screen.getByTestId('banner-failed');
        expect(banner.textContent).toContain('no coordinator today');
        expect(screen.queryByTestId('sigma-card')).toBeNull();
        expect(screen.queryByTestId('agent-details')).toBeNull();
        expect(screen.queryByTestId('tool-indicator')).toBeNull();
    });

    it('a stored session id whose data has not arrived yet shows loading, then a poll error with Retry', () => {
        const { value } = renderWithSession(<AnalysisPanel />, null, { sessionId: 'sess_x', pollError: 'Cannot reach the Kai backend. Is it running?' });
        expect(screen.getByTestId('banner-loading').textContent).toContain('Cannot reach the Kai backend');
        expect(value.sessionId).toBe('sess_x');
    });

    it('a session for another ticker is hidden on that ticker\'s page', () => {
        const { container } = renderWithSession(<AnalysisPanel symbol="MSFT" />, fixtures.holdCompleted);
        expect(container.textContent).toBe('');
    });

    it('lost contact keeps the last data visible and offers Retry', () => {
        const { value } = renderWithSession(<AnalysisPanel />, fixtures.holdCompleted, { pollError: 'Cannot reach the Kai backend. Is it running?' });
        const banner = screen.getByTestId('banner-poll-error');
        expect(banner.textContent).toContain('Lost contact');
        fireEvent.click(within(banner).getByText('Retry'));
        expect(value.retryPolling).toHaveBeenCalledTimes(1);
        expect(screen.getByTestId('sigma-card')).toBeTruthy();
    });
});

describe('human approval', () => {
    const session = fixtures.waitingApproval;

    it('shows the real challenge and wires Approve and Reject to the backend actions', () => {
        const { value } = renderWithSession(<><AnalysisPanel /><MandateModal /></>, session);
        const challenge = session.step_up_challenge;
        expect(document.body.textContent).toContain(challenge.challenge_id);
        expect(document.body.textContent).toContain(challenge.message);
        fireEvent.click(screen.getByText('Approve'));
        expect(value.approve).toHaveBeenCalledTimes(1);
        fireEvent.click(screen.getByText('Reject'));
        expect(value.reject).toHaveBeenCalledTimes(1);
        expect(screen.getByTestId('banner-approval').textContent).toContain('Waiting for your approval');
    });

    it('stays closed once dismissed, and can be reopened from the banner', () => {
        const { value } = renderWithSession(<><AnalysisPanel /><MandateModal /></>, session,
            { dismissedChallenge: session.step_up_challenge.challenge_id });
        expect(screen.queryByText('Approve')).toBeNull();
        fireEvent.click(within(screen.getByTestId('banner-approval')).getByText('Review'));
        expect(value.reopenApproval).toHaveBeenCalledTimes(1);
    });

    it('does not offer approval for an expired challenge', () => {
        const expired = { ...session, step_up_challenge: { ...session.step_up_challenge, status: 'expired' } };
        renderWithSession(<><AnalysisPanel /><MandateModal /></>, expired);
        expect(screen.queryByText('Approve')).toBeNull();
        expect(screen.getByTestId('banner-approval').textContent).toContain('window expired');
    });

    it('shows a backend error from a failed decision', () => {
        renderWithSession(<MandateModal />, session, { approvalError: 'challenge is expired, not pending' });
        expect(document.body.textContent).toContain('challenge is expired, not pending');
    });

    it('does not render a modal for sessions with nothing to approve', () => {
        renderWithSession(<MandateModal />, fixtures.holdCompleted);
        expect(document.body.querySelector('.modal')).toBeNull();
    });
});
