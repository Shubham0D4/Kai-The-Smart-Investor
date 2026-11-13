import React from 'react';
import { Button, Spinner } from 'react-bootstrap';
import { useCommittee } from '../../context/committee';
import { AGENTS, NO_VERDICT_TITLES } from '../../utils/format';

const Alert = ({ tone, testid, children, action }) => (
    <div className={`kai-alert ${tone} d-flex justify-content-between align-items-start gap-3`} data-testid={testid} role="status">
        <div>{children}</div>
        {action}
    </div>
);

/**
 * The state of the session in words: loading, running, failed, stopped by the circuit breaker
 * (or another guard), waiting for approval, rejected, approved. Several can apply at once.
 */
const SessionBanner = () => {
    const { sessionId, session, debate, pollError, retryPolling, clearSession, reopenApproval } = useCommittee();
    if (!sessionId) return null;

    if (!session) {
        return (
            <Alert tone="neutral" testid="banner-loading">
                <span className="d-inline-flex align-items-center gap-2"><Spinner animation="border" size="sm" /> Loading analysis…</span>
                {pollError && <div className="mt-1 neg-text">{pollError}</div>}
            </Alert>
        );
    }

    const outcome = debate?.outcome;
    const dropped = debate ? Object.values(debate.agents).filter((a) => a.state === 'dropped') : [];
    const challenge = session.step_up_challenge;

    return (
        <div className="d-flex flex-column gap-2" data-testid="session-banner">
            {pollError && (
                <Alert tone="warn" testid="banner-poll-error"
                    action={<Button size="sm" variant="outline-secondary" onClick={retryPolling}>Retry</Button>}>
                    <strong>Lost contact with the backend.</strong> The view may be out of date. {pollError}
                </Alert>
            )}

            {session.status === 'running' && (
                <Alert tone="info" testid="banner-running">
                    <span className="d-inline-flex align-items-center gap-2">
                        <Spinner animation="border" size="sm" />
                        <strong>Analysing {session.ticker}</strong>
                        <span>{debate?.rounds_started > 0 ? `· round ${debate.rounds_started} of 3` : '· fetching data'}</span>
                    </span>
                    {session.progress && <div className="mt-1" style={{ opacity: 0.85 }}>{session.progress}</div>}
                </Alert>
            )}

            {session.status === 'failed' && (
                <Alert tone="neg" testid="banner-failed"
                    action={<Button size="sm" variant="outline-secondary" onClick={clearSession}>Dismiss</Button>}>
                    <strong>The analysis failed.</strong>
                    <div className="mt-1" style={{ overflowWrap: 'anywhere' }}>{session.error || 'No error message was recorded.'}</div>
                </Alert>
            )}

            {outcome?.status === 'NO_VERDICT' && (
                <Alert tone="warn" testid="banner-no-verdict">
                    <strong>{NO_VERDICT_TITLES[outcome.reason] || `Stopped without a verdict (${outcome.reason})`}</strong>
                    {outcome.detail && <div className="mt-1">{outcome.detail}</div>}
                    {dropped.length > 0 && (
                        <ul className="mb-0 mt-1 ps-3">
                            {dropped.map((a) => <li key={a.agent}>{AGENTS[a.agent].name} dropped: {a.dropped_reason}</li>)}
                        </ul>
                    )}
                    <div className="mt-2" style={{ opacity: 0.85 }}>No recommendation was produced, and none is guessed.</div>
                </Alert>
            )}

            {session.status === 'waiting_approval' && challenge && (
                <Alert tone="warn" testid="banner-approval"
                    action={challenge.status === 'pending' ? <Button size="sm" variant="primary" onClick={reopenApproval}>Review</Button> : null}>
                    {challenge.status === 'expired'
                        ? <><strong>The approval window expired.</strong> This recommendation can no longer be approved.</>
                        : <><strong>Waiting for your approval.</strong> {challenge.message}</>}
                </Alert>
            )}

            {session.status === 'rejected' && <Alert tone="neutral" testid="banner-rejected">You rejected this recommendation.</Alert>}
            {session.approval_status === 'approved' && <Alert tone="pos" testid="banner-approved">You approved this recommendation.</Alert>}
        </div>
    );
};

export default SessionBanner;
