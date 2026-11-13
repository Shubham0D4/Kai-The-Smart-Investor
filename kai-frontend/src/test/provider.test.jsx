import React, { useEffect } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render } from '@testing-library/react';
import { CommitteeProvider } from '../context/CommitteeContext';
import { useCommittee } from '../context/committee';
import { ApiError } from '../services/api';
import * as api from '../services/api';
import { committeeValue, fixtures } from './helpers';

vi.mock('../services/api', async (importOriginal) => {
    const actual = await importOriginal();
    return {
        ...actual,
        initiateAnalysis: vi.fn(),
        getAnalysisStatus: vi.fn(),
        approveMandate: vi.fn(),
        rejectMandate: vi.fn(),
    };
});

// Captures the context value after each render (in an effect: assigning during render is impure).
let latest;
const Probe = () => {
    const value = useCommittee();
    useEffect(() => { latest = value; });
    return null;
};

const mount = () => render(<CommitteeProvider><Probe /></CommitteeProvider>);
const advance = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const running = { ...fixtures.running, session_id: 'sess_1' };
const completed = { ...fixtures.holdCompleted, session_id: 'sess_1' };

beforeEach(() => {
    vi.useFakeTimers();
    window.localStorage.clear();
});
afterEach(() => {
    cleanup();
    vi.useRealTimers();
});

describe('CommitteeProvider', () => {
    it('exposes exactly the value shape the test helper (and so every component test) assumes', () => {
        mount();
        expect(Object.keys(latest).sort()).toEqual(Object.keys(committeeValue(null)).sort());
    });

    it('starts an analysis, polls while it runs, shows live state, and stops when it completes', async () => {
        api.initiateAnalysis.mockResolvedValue({ session_id: 'sess_1' });
        api.getAnalysisStatus.mockResolvedValueOnce(running).mockResolvedValue(completed);
        mount();

        await act(async () => { await latest.startAnalysis('AAPL', 'Zen'); });
        expect(api.initiateAnalysis).toHaveBeenCalledWith('AAPL', 'Zen');
        expect(window.localStorage.getItem('kai_session_id')).toBe('sess_1');
        await advance(0);
        expect(latest.session.status).toBe('running');
        expect(latest.debate.rounds_started).toBe(1);                 // live structured state

        await advance(2000);
        expect(latest.session.status).toBe('completed');
        expect(latest.sigma.final_recommendation.recommendation).toBe('Hold');
        const calls = api.getAnalysisStatus.mock.calls.length;
        await advance(20000);
        expect(api.getAnalysisStatus.mock.calls.length).toBe(calls);   // terminal state: polling stopped
    });

    it('resumes the stored session after a page reload', async () => {
        window.localStorage.setItem('kai_session_id', 'sess_1');
        api.getAnalysisStatus.mockResolvedValue(completed);
        mount();
        await advance(0);
        expect(api.getAnalysisStatus).toHaveBeenCalledWith('sess_1');
        expect(latest.session.status).toBe('completed');
    });

    it('forgets a stored session the server does not have (404)', async () => {
        window.localStorage.setItem('kai_session_id', 'sess_gone');
        api.getAnalysisStatus.mockRejectedValue(new ApiError('Session not found', 404));
        mount();
        await advance(0);
        expect(latest.sessionId).toBeNull();
        expect(window.localStorage.getItem('kai_session_id')).toBeNull();
    });

    it('reports lost contact, stops after repeated failures, and restarts on Retry', async () => {
        window.localStorage.setItem('kai_session_id', 'sess_1');
        api.getAnalysisStatus.mockRejectedValue(new ApiError('Cannot reach the Kai backend. Is it running?', undefined));
        mount();
        await advance(0);
        expect(latest.pollError).toMatch(/Cannot reach/);
        await advance(2000 * 10);
        expect(api.getAnalysisStatus).toHaveBeenCalledTimes(5);        // gave up after 5 failures
        await advance(20000);
        expect(api.getAnalysisStatus).toHaveBeenCalledTimes(5);

        api.getAnalysisStatus.mockResolvedValue(completed);
        await act(async () => { latest.retryPolling(); });
        await advance(0);
        expect(latest.pollError).toBeNull();
        expect(latest.session.status).toBe('completed');
    });

    it('keeps the last good data when a refresh fails', async () => {
        window.localStorage.setItem('kai_session_id', 'sess_1');
        api.getAnalysisStatus.mockResolvedValueOnce(running).mockRejectedValue(new ApiError('boom', 500));
        mount();
        await advance(0);
        await advance(2000);
        expect(latest.pollError).toBe('boom');
        expect(latest.session.status).toBe('running');                 // not wiped
    });

    it('surfaces a failed start as an error and creates no session', async () => {
        api.initiateAnalysis.mockRejectedValue(new ApiError('ticker: String should match pattern', 422));
        mount();
        await act(async () => { await latest.startAnalysis('???', 'Zen'); });
        expect(latest.startError).toMatch(/should match pattern/);
        expect(latest.sessionId).toBeNull();
        expect(api.getAnalysisStatus).not.toHaveBeenCalled();
    });

    it('approve calls the backend with the challenge id, then reloads the session', async () => {
        const waiting = { ...fixtures.waitingApproval, session_id: 'sess_w' };
        window.localStorage.setItem('kai_session_id', 'sess_w');
        api.getAnalysisStatus.mockResolvedValueOnce(waiting)
            .mockResolvedValue({ ...waiting, status: 'completed', approval_status: 'approved' });
        api.approveMandate.mockResolvedValue({ ready_for_execution: true });
        mount();
        await advance(0);
        expect(latest.session.status).toBe('waiting_approval');
        await act(async () => { await latest.approve(); });
        expect(api.approveMandate).toHaveBeenCalledWith('sess_w', waiting.step_up_challenge.challenge_id);
        expect(latest.session.status).toBe('completed');
        expect(latest.approvalError).toBeNull();
    });

    it('shows the backend message when an approval is refused, and refreshes the session', async () => {
        const waiting = { ...fixtures.waitingApproval, session_id: 'sess_w' };
        window.localStorage.setItem('kai_session_id', 'sess_w');
        api.getAnalysisStatus.mockResolvedValue(waiting);
        api.rejectMandate.mockRejectedValue(new ApiError('challenge is expired, not pending', 400));
        mount();
        await advance(0);
        await act(async () => { await latest.reject(); });
        expect(latest.approvalError).toBe('challenge is expired, not pending');
        expect(api.getAnalysisStatus.mock.calls.length).toBeGreaterThan(1);
    });

    it('works when localStorage is unavailable', async () => {
        const original = Storage.prototype.getItem;
        Storage.prototype.getItem = () => { throw new Error('blocked'); };
        try {
            mount();
            expect(latest.sessionId).toBeNull();
        } finally {
            Storage.prototype.getItem = original;
        }
    });
});
