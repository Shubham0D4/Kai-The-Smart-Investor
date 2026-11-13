import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CommitteeContext } from './committee';
import {
    approveMandate, getAnalysisStatus, initiateAnalysis, rejectMandate,
} from '../services/api';

const STORAGE_KEY = 'kai_session_id';
const POLL_MS = 2000;
const MAX_POLL_FAILURES = 5;

// localStorage can be blocked or empty (private windows): the app must work without it.
const readStoredSession = () => {
    try {
        return window.localStorage.getItem(STORAGE_KEY);
    } catch {
        return null;
    }
};
const storeSession = (id) => {
    try {
        if (id) window.localStorage.setItem(STORAGE_KEY, id);
        else window.localStorage.removeItem(STORAGE_KEY);
    } catch {
        /* per-viewer convenience only */
    }
};

/**
 * Holds the one analysis session the UI is showing. Everything it exposes comes from the
 * backend's /status response (which is built from the database and the persisted debate log):
 * nothing is simulated, canned or randomly generated on the client.
 */
export const CommitteeProvider = ({ children }) => {
    const [sessionId, setSessionId] = useState(readStoredSession);
    const [session, setSession] = useState(null);
    const [starting, setStarting] = useState(false);
    const [startError, setStartError] = useState(null);
    const [pollError, setPollError] = useState(null);
    const [approvalError, setApprovalError] = useState(null);
    const [decidingApproval, setDecidingApproval] = useState(false);
    const [dismissedChallenge, setDismissedChallenge] = useState(null);
    const [pollNonce, setPollNonce] = useState(0);        // bumped by 'retry' to restart a stopped poll loop
    const failures = useRef(0);

    const fetchStatus = useCallback(async (id) => {
        try {
            const data = await getAnalysisStatus(id);
            failures.current = 0;
            setPollError(null);
            setSession(data);
            return data;
        } catch (error) {
            if (error.status === 404) {                      // a stored id the server no longer has
                storeSession(null);
                setSessionId(null);
                setSession(null);
                return null;
            }
            failures.current += 1;
            setPollError(error.message);
            return undefined;
        }
    }, []);

    // Poll while the analysis runs. Terminal states (completed, failed, rejected, waiting for the
    // user) do not change on their own, so polling stops there and resumes if a new run starts.
    const status = session?.status;
    const polling = Boolean(sessionId) && (status === undefined || status === 'running');
    useEffect(() => {
        if (!polling) return undefined;
        let cancelled = false;
        let timer;
        const tick = async () => {
            await fetchStatus(sessionId);
            if (cancelled || failures.current >= MAX_POLL_FAILURES) return;
            timer = setTimeout(tick, POLL_MS);
        };
        tick();
        return () => {
            cancelled = true;
            clearTimeout(timer);
        };
    }, [polling, sessionId, fetchStatus, pollNonce]);

    const startAnalysis = useCallback(async (ticker, persona) => {
        setStarting(true);
        setStartError(null);
        try {
            const created = await initiateAnalysis(ticker, persona);
            failures.current = 0;
            storeSession(created.session_id);
            setSession(null);
            setPollError(null);
            setApprovalError(null);
            setDismissedChallenge(null);
            setSessionId(created.session_id);
            return created;
        } catch (error) {
            setStartError(error.message);
            return null;
        } finally {
            setStarting(false);
        }
    }, []);

    const loadSession = useCallback((id) => {
        failures.current = 0;
        storeSession(id);
        setSession(null);
        setPollError(null);
        setApprovalError(null);
        setDismissedChallenge(null);
        setSessionId(id);
    }, []);

    const clearSession = useCallback(() => {
        storeSession(null);
        setSession(null);
        setSessionId(null);
        setPollError(null);
    }, []);

    const retryPolling = useCallback(() => {
        failures.current = 0;
        setPollError(null);
        setPollNonce((n) => n + 1);
    }, []);

    const decide = useCallback(async (decision) => {
        const challenge = session?.step_up_challenge;
        if (!session || !challenge) return false;
        setDecidingApproval(true);
        setApprovalError(null);
        try {
            if (decision === 'approve') await approveMandate(session.session_id, challenge.challenge_id);
            else await rejectMandate(session.session_id);
            await fetchStatus(session.session_id);
            return true;
        } catch (error) {
            setApprovalError(error.message);
            await fetchStatus(session.session_id);          // e.g. the challenge expired meanwhile
            return false;
        } finally {
            setDecidingApproval(false);
        }
    }, [session, fetchStatus]);

    const value = useMemo(() => ({
        sessionId,
        session,
        debate: session?.debate ?? null,
        sigma: session?.sigma ?? null,
        currentTicker: session?.ticker ?? null,
        starting,
        startError,
        pollError,
        approvalError,
        decidingApproval,
        dismissedChallenge,
        startAnalysis,
        loadSession,
        clearSession,
        retryPolling,
        approve: () => decide('approve'),
        reject: () => decide('reject'),
        dismissApproval: (challengeId) => setDismissedChallenge(challengeId),
        reopenApproval: () => setDismissedChallenge(null),
    }), [sessionId, session, starting, startError, pollError, approvalError, decidingApproval,
        dismissedChallenge, startAnalysis, loadSession, clearSession, retryPolling, decide]);

    return <CommitteeContext.Provider value={value}>{children}</CommitteeContext.Provider>;
};

export default CommitteeProvider;
