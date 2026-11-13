import React from 'react';
import { OverlayTrigger, Tooltip } from 'react-bootstrap';
import Chip from '../../components/Chip';
import { DASH, num, pct, usd } from '../../utils/format';

// How each field is shown. Units in the report are generic ("ratio"), so display is by field id.
const FMT = {
    pct: (v) => pct(v, 1),
    pct2: (v) => pct(v, 2),
    num: (v) => num(v, 2),
    num3: (v) => num(v, 3),
    usd: (v) => usd(v, 2),
};

// Wording in the legend; the short form is used in the table.
const KIND = {
    descriptive: { legend: 'describes the past', short: 'Past', tone: 'neutral' },
    model: { legend: 'model (assumptions)', short: 'Model', tone: 'info' },
    prediction: { legend: 'prediction', short: 'Forecast', tone: 'warn' },
};

const GROUPS = [
    {
        title: 'Return distribution',
        sub: 'Last year of daily returns',
        rows: [
            ['quant:returns.vol_annual', 'pct'], ['quant:returns.skew', 'num'], ['quant:returns.excess_kurtosis', 'num'],
            ['quant:returns.max_drawdown', 'pct'], ['quant:returns.var95_hist', 'pct2'], ['quant:returns.var95_normal', 'pct2'],
        ],
    },
    {
        title: 'Relationship to the S&P 500',
        sub: 'Regression of daily returns',
        rows: [
            ['quant:market.beta', 'num3'], ['quant:market.beta_se', 'num3'], ['quant:market.correlation', 'num'],
            ['quant:market.alpha_daily', 'pct2'], ['quant:capm.discount_rate', 'pct2'],
        ],
    },
    {
        title: 'Monte Carlo DCF',
        sub: 'Fair value per share under sampled growth and discount rates',
        rows: [
            ['quant:mc_dcf.fair_value_p10', 'usd'], ['quant:mc_dcf.fair_value_p50', 'usd'],
            ['quant:mc_dcf.fair_value_p90', 'usd'], ['quant:mc_dcf.prob_fair_above_price', 'pct'],
        ],
    },
    {
        title: 'Forward simulation',
        sub: 'Probability the price is above today’s at the horizon',
        rows: [
            ['quant:forward.prob_up_bootstrap', 'pct'], ['quant:forward.prob_up_normal', 'pct'],
            ['quant:forward.return_p5', 'pct'], ['quant:forward.return_p50', 'pct'], ['quant:forward.return_p95', 'pct'],
        ],
    },
    {
        title: 'Base rate and calibration',
        sub: 'How often similar situations rose; how well past forecasts matched outcomes',
        rows: [['quant:base_rate.share_up', 'pct'], ['quant:calibration.brier', 'num3']],
    },
];

const sampleText = (f) => (f.n !== null && f.n !== undefined ? `${f.n.toLocaleString()} ${f.n_kind}` : f.n_kind);

const Row = ({ field, fmtName }) => {
    const fmt = FMT[fmtName];
    const kind = KIND[field.kind];
    const method = (
        <Tooltip id={`method-${field.id}`}>
            {field.method}
            {field.status === 'unavailable' ? ` — unavailable: ${field.reason}` : ''}
        </Tooltip>
    );
    return (
        <tr data-testid={`quant-${field.id}`}>
            <td>
                <OverlayTrigger placement="top" overlay={method}>
                    <span style={{ cursor: 'help', borderBottom: '1px dotted var(--border-strong)' }}>{field.label}</span>
                </OverlayTrigger>
            </td>
            {field.status === 'ok' ? (
                <>
                    <td className="num fw-semibold">{fmt(field.value)}</td>
                    <td className="num muted">
                        {field.interval
                            ? `${fmt(field.interval[0])} – ${fmt(field.interval[1])} (${Math.round(field.interval_level * 100)}%)`
                            : DASH}
                    </td>
                </>
            ) : (
                <td colSpan={2} className="muted" style={{ fontStyle: 'italic' }}>unavailable: {field.reason}</td>
            )}
            <td className="num muted">{sampleText(field)}</td>
            <td><Chip tone={kind.tone}>{kind.short}</Chip></td>
        </tr>
    );
};

const ReliabilityTable = ({ bins }) => (
    <table className="kai-table">
        <thead><tr><th>Stated probability</th><th className="num">Forecasts</th><th className="num">Mean stated</th><th className="num">Observed share up</th></tr></thead>
        <tbody>
            {bins.filter((b) => b.n > 0).map((b) => (
                <tr key={b.low}>
                    <td>{pct(b.low, 0)} – {pct(b.high, 0)}</td><td className="num">{b.n}</td>
                    <td className="num">{pct(b.mean_stated, 0)}</td><td className="num">{pct(b.observed_share, 0)}</td>
                </tr>
            ))}
        </tbody>
    </table>
);

/** Position of the fair-value range relative to the current price, with a legend that matches the marks. */
const RangeBar = ({ p10, p50, p90, price }) => {
    const lo = Math.min(p10, price);
    const hi = Math.max(p90, price);
    const at = (v) => `${((v - lo) / (hi - lo || 1)) * 100}%`;
    const legend = [
        ['range', 'p10 – p90 range', `${usd(p10)} – ${usd(p90)}`],
        ['median', 'Median fair value', usd(p50)],
        ['price', 'Last price', usd(price)],
    ];
    return (
        <div aria-label="Fair value range against current price" className="kai-tile mb-4">
            <div className="kai-label">Fair value range vs. last price</div>
            <div className="position-relative my-3" style={{ height: 8, background: 'var(--surface-3)', borderRadius: 999 }}>
                <div className="position-absolute" style={{ left: at(p10), width: `calc(${at(p90)} - ${at(p10)})`, top: 0, bottom: 0, background: 'var(--accent)', opacity: 0.35, borderRadius: 999 }} />
                <div className="position-absolute" style={{ left: at(p50), width: 3, top: -4, bottom: -4, background: 'var(--accent)', borderRadius: 2 }} />
                <div className="position-absolute" style={{ left: at(price), width: 3, top: -6, bottom: -6, background: 'var(--neg)', borderRadius: 2 }} />
            </div>
            <div className="d-flex flex-wrap gap-4 small">
                {legend.map(([kind, label, value]) => (
                    <span key={kind} className="d-inline-flex align-items-center gap-2">
                        <span style={{ width: kind === 'range' ? 16 : 3, height: kind === 'range' ? 8 : 14, borderRadius: 2, flexShrink: 0,
                            background: kind === 'price' ? 'var(--neg)' : 'var(--accent)', opacity: kind === 'range' ? 0.35 : 1 }} />
                        <span style={{ color: 'var(--text-2)' }}>{label}</span>
                        <span className="fw-semibold">{value}</span>
                    </span>
                ))}
            </div>
        </div>
    );
};

/**
 * The deterministic quant report: ranges and probabilities with the sample size behind each,
 * and whether each number describes the past, follows from assumptions, or is a prediction.
 */
const QuantPanel = ({ report, price }) => {
    if (!report) return null;
    const p10 = report.fields['quant:mc_dcf.fair_value_p10'];
    const p50 = report.fields['quant:mc_dcf.fair_value_p50'];
    const p90 = report.fields['quant:mc_dcf.fair_value_p90'];
    const showRange = [p10, p50, p90].every((f) => f && f.status === 'ok') && Number.isFinite(price);
    const drift = report.drift_mode === 'zero' ? 'past trend removed' : 'past trend carried forward';

    return (
        <section className="kai-card" data-testid="quant-panel">
            <div className="kai-card-head">
                <div>
                    <h2 className="kai-card-title">Quant report</h2>
                    <div className="kai-card-sub">
                        Computed in code from fetched data (no LLM) · as of {report.as_of} · seed {report.seed} ·
                        {' '}{report.horizon_days}-day horizon · forward simulations: {drift}
                    </div>
                </div>
                <div className="d-none d-md-flex gap-2 flex-shrink-0">
                    {Object.values(KIND).map((k) => <Chip key={k.legend} tone={k.tone}>{k.legend}</Chip>)}
                </div>
            </div>
            <div className="kai-card-body">
                {showRange && <RangeBar p10={p10.value} p50={p50.value} p90={p90.value} price={price} />}
                <div className="table-responsive">
                    {GROUPS.map((group, i) => (
                        <div key={group.title} className={i ? 'mt-4' : ''}>
                            <div className="d-flex align-items-baseline gap-2 mb-1">
                                <h3 className="mb-0" style={{ fontSize: 'var(--fs-base)' }}>{group.title}</h3>
                                <span className="small text-muted">{group.sub}</span>
                            </div>
                            <table className="kai-table" style={{ minWidth: 640 }}>
                                <colgroup><col /><col style={{ width: 110 }} /><col style={{ width: 190 }} /><col style={{ width: 150 }} /><col style={{ width: 90 }} /></colgroup>
                                <thead><tr><th>Measure</th><th className="num">Value</th><th className="num">Interval</th><th className="num">Sample</th><th>Basis</th></tr></thead>
                                <tbody>
                                    {group.rows.map(([id, fmtName]) => {
                                        const field = report.fields[id];
                                        return field ? <Row key={id} field={field} fmtName={fmtName} /> : null;
                                    })}
                                </tbody>
                            </table>
                        </div>
                    ))}
                </div>
                {report.reliability && (
                    <div className="mt-4">
                        <h3 style={{ fontSize: 'var(--fs-base)' }}>Reliability of past forecasts</h3>
                        <ReliabilityTable bins={report.reliability} />
                    </div>
                )}
                {report.notes?.length > 0 && (
                    <ul className="small text-muted mb-0 mt-3 ps-3">{report.notes.map((n) => <li key={n}>{n}</li>)}</ul>
                )}
                <div className="small text-muted mt-2">Hover a measure for how it is computed.</div>
            </div>
        </section>
    );
};

export default QuantPanel;
