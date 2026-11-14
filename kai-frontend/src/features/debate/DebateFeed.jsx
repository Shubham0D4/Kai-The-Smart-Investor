import React, { useRef } from 'react';
import { Download } from 'lucide-react';
import jsPDF from 'jspdf';
import html2canvas from 'html2canvas';
import { useCommittee } from '../../context/committee';
import Chip from '../../components/Chip';
import { clock } from '../../utils/format';

const AGENT_LABEL = { charlie: 'Charlie', delta: 'Delta', gamma: 'Gamma', vega: 'Vega', sigma: 'Sigma', system: 'System' };
const AGENT_TONE = { charlie: 'accent', delta: 'info', gamma: 'pos', vega: 'plain', sigma: 'warn', system: 'neutral' };

const AgentBadge = ({ agent }) => (
    <Chip tone={AGENT_TONE[agent] || 'neutral'} className="justify-content-center" style={{ minWidth: 64 }}>
        {AGENT_LABEL[agent] || agent}
    </Chip>
);

const PDF_MARGIN_MM = 10;

/** Render the transcript to a multi-page PDF. */
const exportToPDF = async (feedElement) => {
    const container = document.createElement('div');
    Object.assign(container.style, {
        position: 'absolute', left: '-9999px', top: '0', width: '800px', backgroundColor: 'white',
        padding: '40px', fontFamily: 'Arial, sans-serif',
    });
    const title = document.createElement('h1');
    title.textContent = 'Debate transcript';
    const stamp = document.createElement('p');
    stamp.textContent = `Generated: ${new Date().toLocaleString()}`;
    const clone = feedElement.cloneNode(true);
    Object.assign(clone.style, { maxHeight: 'none', overflow: 'visible', border: 'none' });
    container.append(title, stamp, clone);
    document.body.appendChild(container);
    try {
        const canvas = await html2canvas(container, { scale: 2, useCORS: true, logging: false, windowWidth: 800 });
        const pdf = new jsPDF('p', 'mm', 'a4');
        const pageW = pdf.internal.pageSize.getWidth();
        const pageH = pdf.internal.pageSize.getHeight();
        const imgW = pageW - 2 * PDF_MARGIN_MM;
        const imgH = (canvas.height * imgW) / canvas.width;
        const data = canvas.toDataURL('image/png');
        const usable = pageH - 2 * PDF_MARGIN_MM;
        let printed = 0;
        while (printed < imgH) {
            if (printed > 0) pdf.addPage();
            pdf.addImage(data, 'PNG', PDF_MARGIN_MM, PDF_MARGIN_MM - printed, imgW, imgH);
            printed += usable;
        }
        pdf.save(`debate-transcript-${new Date().toISOString().split('T')[0]}.pdf`);
    } finally {
        document.body.removeChild(container);
    }
};

/** The debate's log lines, exactly as persisted by the coordinator. */
const DebateFeed = () => {
    const { session, debate } = useCommittee();
    const feedRef = useRef(null);
    const events = debate?.events || [];
    if (events.length === 0) return null;
    const running = session?.status === 'running';

    const handleExport = async () => {
        try {
            await exportToPDF(feedRef.current);
        } catch (error) {
            console.error('Error generating PDF:', error);
            window.alert('Failed to generate the PDF.');
        }
    };

    return (
        <section className="kai-card" data-testid="debate-feed">
            <div className="kai-card-head">
                <div className="d-flex align-items-center gap-2">
                    {running && <span className="kai-dot pos pulse" />}
                    <div>
                        <h2 className="kai-card-title">{running ? 'Live debate log' : 'Debate log'}</h2>
                        <div className="kai-card-sub">{events.length} entries, exactly as recorded by the coordinator</div>
                    </div>
                </div>
                <button type="button" onClick={handleExport} className="btn btn-sm btn-outline-secondary d-inline-flex align-items-center gap-2">
                    <Download size={14} /> Export PDF
                </button>
            </div>
            <div className="custom-scrollbar" style={{ maxHeight: 480, overflowY: 'auto' }}>
                <ul className="list-unstyled mb-0 px-4 py-2" ref={feedRef}>
                    {events.map((e, i) => (
                        <li key={`${e.timestamp}-${i}`} className="d-flex gap-3 py-3" style={{ borderBottom: i < events.length - 1 ? '1px solid var(--border)' : 0 }}>
                            <div className="flex-shrink-0 pt-1"><AgentBadge agent={e.agent} /></div>
                            <div style={{ minWidth: 0 }}>
                                <div className="small text-muted mb-1">{e.round ? `Round ${e.round} · ` : ''}{clock(e.timestamp)}</div>
                                <div className={e.agent === 'system' ? 'small text-muted' : 'small'} style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', color: e.agent === 'system' ? undefined : 'var(--text)' }}>
                                    {e.message}
                                </div>
                            </div>
                        </li>
                    ))}
                </ul>
            </div>
        </section>
    );
};

export default DebateFeed;
