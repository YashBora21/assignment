import React, { useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, Info, XCircle } from 'lucide-react';

const STATUS_META = {
    consistent: { label: 'Consistent', color: '#16a34a', icon: CheckCircle2 },
    refines: { label: 'Refines', color: '#2563eb', icon: Info },
    additional: { label: 'Additional', color: '#6b7280', icon: Info },
    conflict: { label: 'Conflict', color: '#dc2626', icon: XCircle },
    unmapped: { label: 'Unmapped', color: '#b91c1c', icon: AlertTriangle },
};

function meta(status) {
    return STATUS_META[status] || { label: status || 'Unknown', color: '#6b7280', icon: Info };
}

function citationLabel(source) {
    if (!source) return 'no citation';
    const document = source.document || 'unknown document';
    const page = source.page ? `p.${source.page}` : '';
    const row = source.row != null ? `row ${source.row}` : '';
    return [document, page, row].filter(Boolean).join(' · ');
}

function valueOf(requirement, sourceType) {
    const assertion = (requirement.assertions || []).find((item) => item.source_type === sourceType);
    if (!assertion) return '—';
    return [assertion.value, assertion.unit].filter(Boolean).join(' ');
}

function StatusPill({ status }) {
    const item = meta(status);
    const Icon = item.icon;
    return (
        <span className="badge" style={{
            display: 'inline-flex', alignItems: 'center', gap: '5px',
            color: item.color, border: `1px solid ${item.color}55`,
            background: `${item.color}12`, fontSize: '11px', fontWeight: 700,
        }}>
            <Icon size={12} />
            {item.label}
        </span>
    );
}

function Section({ title, subtitle, children }) {
    return (
        <section style={{ marginTop: '26px' }}>
            <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 600, color: 'var(--text-ink)' }}>{title}</h3>
            {subtitle && <p style={{ margin: '4px 0 10px', color: 'var(--text-muted)', fontSize: '13px' }}>{subtitle}</p>}
            {children}
        </section>
    );
}

export default function ReconciliationPanel({ reconciliation }) {
    const [filter, setFilter] = useState('all');
    const requirements = reconciliation?.canonical_requirements || [];
    const conflicts = reconciliation?.conflicts || [];
    const compliance = reconciliation?.compliance_requirements || [];
    const eligibility = reconciliation?.vendor_eligibility || [];
    const coverage = reconciliation?.coverage || {};
    const summary = reconciliation?.summary || {};
    const manualRequired = reconciliation?.manual_required || [];
    const reviewNotes = reconciliation?.review_notes || [];
    const sources = reconciliation?.sources || {};
    const readiness = reconciliation?.status || (requirements.length ? 'ready' : 'no_requirements');
    const readyForMatching = readiness === 'ready';
    const attachmentNotes = reviewNotes.filter((note) => note?.type === 'attachment_failure');
    const sourceLabels = [
        ['specification', 'Spec'],
        ['boq', 'BOQ'],
        ['wrapper', 'Wrapper'],
    ].filter(([key]) => sources[key]);

    const visible = useMemo(
        () => (filter === 'all' ? requirements : requirements.filter((row) => row.status === filter)),
        [requirements, filter],
    );

    if (!reconciliation) {
        return <div className="empty-state" role="status">No reconciliation table is available for this tender.</div>;
    }

    return (
        <div style={{ padding: '28px 32px', height: '100%', overflow: 'auto' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '20px', flexWrap: 'wrap' }}>
                <div>
                    <h2 style={{ margin: 0, fontSize: '24px', fontWeight: 300, letterSpacing: '-0.02em' }}>
                        Cross-Document Reconciliation
                    </h2>
                    <p style={{ color: 'var(--text-muted)', marginTop: '6px', fontSize: '13px' }}>
                        Specification · BOQ · wrapper — compared before any manufacturer is scored.
                    </p>
                </div>
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
                    <span className={`badge ${readyForMatching ? 'done' : 'warning'}`}>
                        {readyForMatching ? 'Ready for matching' : 'No requirements extracted'}
                    </span>
                    <span className="badge" title="Documents that contributed to this reconciliation">
                        Sources: {sourceLabels.length ? sourceLabels.map(([, label]) => label).join(' · ') : 'none'}
                    </span>
                </div>
            </div>

            {attachmentNotes.length > 0 && (
                <div style={{
                    marginTop: '18px', padding: '12px 16px', borderRadius: '12px',
                    border: '1px solid #d9770655', background: '#d9770610', color: '#92400e', fontSize: '13px',
                }}>
                    <strong>Some documents could not be read — matching uses what was recovered</strong>
                    <ul style={{ margin: '6px 0 0 18px', padding: 0 }}>
                        {attachmentNotes.map((note, index) => <li key={index}>{note.message}</li>)}
                    </ul>
                </div>
            )}

            {manualRequired.length > 0 && (
                <div style={{
                    marginTop: '14px', padding: '12px 16px', borderRadius: '12px',
                    border: '1px solid #d9770655', background: '#d9770610', color: '#92400e', fontSize: '13px',
                }}>
                    <strong>Manual upload required for these documents</strong>
                    <ul style={{ margin: '6px 0 0 18px', padding: 0 }}>
                        {manualRequired.map((entry, index) => (
                            <li key={index}>
                                <strong>{entry.role}</strong>: {entry.reason}
                                {entry.url && <span style={{ color: 'var(--text-muted)' }}> ({entry.url})</span>}
                            </li>
                        ))}
                    </ul>
                </div>
            )}

            <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', marginTop: '20px' }}>
                {Object.entries(STATUS_META).map(([status, item]) => (
                    <button
                        key={status}
                        onClick={() => setFilter(filter === status ? 'all' : status)}
                        className="outline-pill"
                        style={{
                            borderColor: filter === status ? item.color : undefined,
                            color: item.color, fontWeight: 700, fontSize: '12px',
                        }}
                        title={`Filter ${item.label.toLowerCase()} requirements`}
                    >
                        {item.label}: {summary[status] || 0}
                    </button>
                ))}
                <span style={{ alignSelf: 'center', color: 'var(--text-muted)', fontSize: '12px' }}>
                    {coverage.mapped_items || 0}/{coverage.items?.length || 0} items mapped
                </span>
            </div>

            {reconciliation.wrapper_candidates?.items?.length > 0 && (
                <Section
                    title={`Wrapper item list (${reconciliation.wrapper_candidates.items.length}, total ${reconciliation.wrapper_candidates.total_quantity})`}
                    subtitle={reconciliation.wrapper_candidates.reason}
                >
                    <div className="table-container" style={{ marginTop: '4px' }}>
                        <table>
                            <thead><tr><th>#</th><th>Item</th><th>Quantity</th><th>Source</th></tr></thead>
                            <tbody>
                                {reconciliation.wrapper_candidates.items.map((item) => (
                                    <tr key={item.item_id}>
                                        <td>{item.item_id}</td>
                                        <td>{item.description}</td>
                                        <td>{[item.quantity, item.unit].filter(Boolean).join(' ')}</td>
                                        <td style={{ fontSize: '12px' }}>{citationLabel(item.source)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </Section>
            )}

            <Section
                title={`Conflicts (${conflicts.length})`}
                subtitle="Both source values and citations are preserved. The canonical value stays unresolved until a human decides."
            >
                {conflicts.length === 0 ? (
                    <div className="empty-state" style={{ padding: '24px' }}>No conflicting values detected.</div>
                ) : (
                    <div className="table-container" style={{ marginTop: '4px' }}>
                        <table>
                            <thead>
                                <tr>
                                    <th style={{ minWidth: '180px' }}>Requirement</th>
                                    <th style={{ minWidth: '150px' }}>Specification</th>
                                    <th style={{ minWidth: '150px' }}>BOQ / wrapper</th>
                                    <th style={{ minWidth: '90px' }}>Severity</th>
                                    <th style={{ minWidth: '220px' }}>Reason</th>
                                </tr>
                            </thead>
                            <tbody>
                                {conflicts.map((row) => (
                                    <tr key={row.requirement_id}>
                                        <td>
                                            <div style={{ fontWeight: 600 }}>{row.parameter}</div>
                                            <div style={{ color: 'var(--text-muted)', fontSize: '12px' }}>{row.component}</div>
                                        </td>
                                        <td>
                                            <div style={{ fontWeight: 600 }}>{valueOf(row, 'specification')}</div>
                                            <div style={{ color: 'var(--text-muted)', fontSize: '11px' }}>
                                                {(row.assertions || []).filter((a) => a.source_type === 'specification')
                                                    .map((a) => citationLabel(a.source)).join(' · ')}
                                            </div>
                                        </td>
                                        <td>
                                            <div style={{ fontWeight: 600 }}>{valueOf(row, 'boq') !== '—' ? valueOf(row, 'boq') : valueOf(row, 'wrapper')}</div>
                                            <div style={{ color: 'var(--text-muted)', fontSize: '11px' }}>
                                                {(row.assertions || []).filter((a) => a.source_type !== 'specification')
                                                    .map((a) => citationLabel(a.source)).join(' · ')}
                                            </div>
                                        </td>
                                        <td style={{ color: row.severity === 'critical' ? '#dc2626' : '#d97706', fontWeight: 700 }}>
                                            {row.severity}
                                        </td>
                                        <td style={{ color: 'var(--text-muted)', fontSize: '12px' }}>{row.reason}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </Section>

            <Section title="Canonical requirements" subtitle="Product requirements used for manufacturer matching.">
                <div className="table-container" style={{ marginTop: '4px', maxHeight: '420px' }}>
                    <table>
                        <thead>
                            <tr>
                                <th>#</th>
                                <th>Item</th>
                                <th>Parameter</th>
                                <th>Value</th>
                                <th>Status</th>
                                <th>Source</th>
                            </tr>
                        </thead>
                        <tbody>
                            {visible.map((row) => (
                                <tr key={row.requirement_id}>
                                    <td style={{ color: 'var(--text-muted-soft)', fontSize: '12px' }}>{row.requirement_id}</td>
                                    <td style={{ fontSize: '12px' }}>{row.boq_item_id || '—'}</td>
                                    <td style={{ fontWeight: 500 }}>{row.parameter}</td>
                                    <td>
                                        <span style={{ fontWeight: 600 }}>{[row.value, row.unit === 'NOT_FOUND' ? '' : row.unit].filter(Boolean).join(' ')}</span>
                                        {row.canonical_value === null && (
                                            <span style={{ color: '#dc2626', fontSize: '11px', marginLeft: '6px' }}>unresolved</span>
                                        )}
                                    </td>
                                    <td><StatusPill status={row.status} /></td>
                                    <td style={{ color: 'var(--text-muted)', fontSize: '11px' }}>{citationLabel(row.source)}</td>
                                </tr>
                            ))}
                            {visible.length === 0 && (
                                <tr><td colSpan={6} style={{ textAlign: 'center', padding: '28px', color: 'var(--text-muted)' }}>No requirements match this filter.</td></tr>
                            )}
                        </tbody>
                    </table>
                </div>
            </Section>

            <Section title={`ATC / technical compliance (${compliance.length})`} subtitle="Kept separate from the product match score.">
                {compliance.length === 0 ? <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>No compliance clauses detected.</div> : (
                    <ul style={{ margin: '4px 0 0 18px', padding: 0, color: 'var(--text-ink)', fontSize: '13px', lineHeight: 1.7 }}>
                        {compliance.map((row) => (
                            <li key={row.requirement_id}>
                                <strong>{row.category}</strong> · {row.severity}{row.mandatory ? ' · mandatory' : ''} — {row.text}
                                <span style={{ color: 'var(--text-muted)' }}> ({citationLabel(row.source)})</span>
                            </li>
                        ))}
                    </ul>
                )}
            </Section>

            <Section title={`Vendor eligibility (${eligibility.length})`} subtitle="Never merged into the technical ranking.">
                {eligibility.length === 0 ? <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>No eligibility clauses detected.</div> : (
                    <ul style={{ margin: '4px 0 0 18px', padding: 0, color: 'var(--text-ink)', fontSize: '13px', lineHeight: 1.7 }}>
                        {eligibility.map((row) => (
                            <li key={row.requirement_id}>
                                <strong>{row.category}</strong> — {row.text}
                            </li>
                        ))}
                    </ul>
                )}
            </Section>

            {coverage.items?.length > 0 && (
                <Section title="Item coverage" subtitle="Every BOQ/wrapper line should map to at least one requirement.">
                    <div className="table-container" style={{ marginTop: '4px' }}>
                        <table>
                            <thead><tr><th>Item</th><th>Description</th><th>Quantity</th><th>Source</th><th>Requirements</th></tr></thead>
                            <tbody>
                                {coverage.items.map((item) => (
                                    <tr key={item.item_id}>
                                        <td>{item.item_id}</td>
                                        <td>{item.description}</td>
                                        <td>{[item.quantity, item.unit].filter(Boolean).join(' ')}</td>
                                        <td style={{ fontSize: '12px' }}>{item.source_type}</td>
                                        <td style={{ fontSize: '12px', color: item.requirement_ids?.length ? 'inherit' : '#dc2626' }}>
                                            {item.requirement_ids?.length ? item.requirement_ids.join(', ') : 'unmapped'}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </Section>
            )}
        </div>
    );
}
