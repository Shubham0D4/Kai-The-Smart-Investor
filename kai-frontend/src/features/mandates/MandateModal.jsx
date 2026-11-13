import React from 'react';
import { Button, Modal } from 'react-bootstrap';
import { CheckCircle2, ShieldAlert, XCircle } from 'lucide-react';
import { useCommittee } from '../../context/committee';
import { dateTime } from '../../utils/format';

/**
 * Human approval for a Buy/Sell the backend flagged as needing it. Shown only for a real,
 * pending step-up challenge of the active session; Approve/Reject call the backend.
 */
const MandateModal = () => {
    const {
        session, approvalError, decidingApproval, dismissedChallenge, approve, reject, dismissApproval,
    } = useCommittee();

    const challenge = session?.step_up_challenge;
    const show = session?.status === 'waiting_approval' && challenge?.status === 'pending'
        && dismissedChallenge !== challenge.challenge_id;
    if (!challenge) return null;

    return (
        <Modal show={show} onHide={() => dismissApproval(challenge.challenge_id)} centered backdrop="static">
            <Modal.Header closeButton>
                <Modal.Title className="d-flex align-items-center gap-2 fs-5 fw-semibold">
                    <ShieldAlert size={20} className="warn-text" /> Approval required
                </Modal.Title>
            </Modal.Header>

            <Modal.Body className="p-4">
                <div className="kai-tile mb-3">
                    <div className="kai-label mb-1">Recommendation</div>
                    <div className="fw-semibold" style={{ fontSize: '1.125rem' }}>
                        {session.ticker}: {challenge.recommendation}
                        {challenge.confidence !== null && challenge.confidence !== undefined ? ` at ${Math.round(challenge.confidence)}% confidence` : ''}
                    </div>
                    <div className="small text-muted font-monospace mt-1">{challenge.challenge_id}</div>
                </div>

                <p>{challenge.message}</p>
                {challenge.key_points.length > 0 && (
                    <ul className="small">{challenge.key_points.map((p) => <li key={p}>{p}</li>)}</ul>
                )}
                <div className="small text-muted">Expires {dateTime(challenge.expires_at)}</div>
                {approvalError && <div className="kai-alert neg mt-3" role="alert">{approvalError}</div>}
            </Modal.Body>

            <Modal.Footer className="d-flex justify-content-between p-3">
                <Button variant="outline-secondary" onClick={reject} disabled={decidingApproval} className="d-inline-flex align-items-center gap-2">
                    <XCircle size={16} /> Reject
                </Button>
                <Button variant="primary" onClick={approve} disabled={decidingApproval} className="d-inline-flex align-items-center gap-2">
                    {decidingApproval ? 'Working…' : <><CheckCircle2 size={16} /> Approve</>}
                </Button>
            </Modal.Footer>
        </Modal>
    );
};

export default MandateModal;
