import React from 'react';
import { OverlayTrigger, Popover } from 'react-bootstrap';
import { Database } from 'lucide-react';
import { withUnit } from '../utils/format';

/**
 * A reference to one evidence record (an id like "sec:revenue:FY2026" or "quant:forward.prob_up_bootstrap").
 * The popover shows the real record from the debate: its value, period and source. An id that is
 * not in the supplied evidence says so instead of inventing a source.
 */
const CitationPopover = ({ evidenceId, evidence = {} }) => {
    const record = evidence[evidenceId];

    const popover = (
        <Popover id={`popover-evidence-${evidenceId}`} className="shadow-lg" style={{ maxWidth: 380 }}>
            <Popover.Header as="h3" className="d-flex align-items-center gap-2">
                <Database size={15} className="text-muted flex-shrink-0" />
                <span className="font-monospace text-break" style={{ fontSize: '0.75rem' }}>{evidenceId}</span>
            </Popover.Header>
            <Popover.Body>
                {record ? (
                    <>
                        <div className="fw-semibold mb-1">{record.label}</div>
                        {record.value !== null && record.value !== undefined && (
                            <div className="font-monospace mb-1">{withUnit(record.value, record.unit)}</div>
                        )}
                        {record.text && (
                            <p className="small text-muted border rounded p-2 mb-2">{record.text}</p>
                        )}
                        {record.period && <div className="small text-muted">Period: {record.period}</div>}
                        <div className="small text-muted text-break">Source: {record.source}</div>
                        {record.url && (
                            <a href={record.url} target="_blank" rel="noreferrer" className="small text-break">
                                Open source
                            </a>
                        )}
                    </>
                ) : (
                    <div className="small neg-text">
                        This id is not in the evidence the agent was given, so it cannot be traced to a source.
                    </div>
                )}
            </Popover.Body>
        </Popover>
    );

    return (
        <OverlayTrigger trigger="click" placement="top" overlay={popover} rootClose>
            <button type="button" className={`kai-evidence${record ? '' : ' missing'}`} title="Click to see the source">
                {evidenceId}
            </button>
        </OverlayTrigger>
    );
};

export default CitationPopover;
