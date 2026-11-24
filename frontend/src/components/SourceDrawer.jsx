import React from 'react';
import { X, FileText } from 'lucide-react';

const STATUS_LABELS = {
    full_match: { label: 'Full match', color: '#16a34a' },
    partial_match: { label: 'Partial match', color: '#d97706' },
    missing_capability: { label: 'Missing capability', color: '#dc2626' },
    unsupported_requirement: { label: 'Unsupported requirement', color: '#6b7280' },
};

export default function SourceDrawer({
    isOpen,
    onClose,
    source,
    title,
    specs = {},
    confidence = 0,
    evidence = null,
    status = '',
    reason = '',
}) {
    if (!isOpen) return <div className="source-drawer"></div>;

    const entries = Object.entries(specs || {}).filter(
        ([, value]) => value !== undefined && value !== null && value !== '' && value !== 'NOT_FOUND'
    );
    const confidenceValue = Number(confidence || 0).toFixed(2);
    const evidenceList = Array.isArray(evidence) ? evidence.filter(Boolean) : [];
    const statusMeta = STATUS_LABELS[status];

    return (
        <div className={`source-drawer ${isOpen ? 'open' : ''}`}>
            <div className="drawer-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <h3>{title}</h3>
                <button
                  className="icon-btn"
                  onClick={onClose}
                  style={{ borderRadius: '50%' }}
                >
                  <X size={20} />
                </button>
            </div>

            <div className="drawer-content">
                {statusMeta && (
                    <div style={{
                        display: 'inline-block', padding: '4px 10px', borderRadius: '999px',
                        fontSize: '11px', fontWeight: 700, color: statusMeta.color,
                        border: `1px solid ${statusMeta.color}55`, background: `${statusMeta.color}12`,
                        marginBottom: '14px',
                    }}>
                        {statusMeta.label}
                    </div>
                )}

                {reason && (
                    <p style={{ margin: '0 0 18px', fontSize: '13px', lineHeight: 1.6, color: 'var(--text-muted)' }}>
                        {reason}
                    </p>
                )}

                <h4 style={{ fontSize: '0.85rem', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '10px' }}>
                    {evidenceList.length > 1 ? 'Evidence Matches' : 'Evidence Match'}
                </h4>

                {evidenceList.length > 0 ? (
                    <>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                        {evidenceList.map((item, index) => (
                            <div key={`${item.chunk_id || 'ev'}-${index}`} style={{ border: '1px solid var(--hairline)', borderRadius: '12px', overflow: 'hidden' }}>
                                <div style={{
                                    display: 'flex', justifyContent: 'space-between', gap: '10px',
                                    padding: '8px 12px', background: 'var(--canvas-soft)',
                                    fontSize: '11px', color: 'var(--text-muted)',
                                }}>
                                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px' }}>
                                        <FileText size={12} /> {item.document || 'Catalogue'}
                                    </span>
                                    <span>Page {item.page || '-'}</span>
                                </div>
                                <div className="source-text-box" style={{ border: 'none', borderRadius: 0 }}>
                                    {item.quote || 'No quote returned.'}
                                </div>
                                {item.chunk_id && item.chunk_id !== 'NOT_FOUND' && (
                                    <div style={{ padding: '6px 12px', fontSize: '10px', color: 'var(--text-muted-soft)', fontFamily: 'var(--font-mono)' }}>
                                        Ref ID: {item.chunk_id}
                                    </div>
                                )}
                            </div>
                        ))}
                    </div>
                    <div style={{ marginTop: '10px', color: 'var(--text-muted)', fontSize: '0.8rem' }}>
                        Confidence: {confidenceValue}
                    </div>
                    </>
                ) : source ? (
                    <>
                        <div className="source-meta">
                            <span><FileText size={14} style={{ display: 'inline', marginBottom: '-2px', marginRight: '5px' }} /> {source.document || 'Source Text'}</span>
                            <span className="badge queued">{source.row != null ? `Row ${source.row}` : `Page ${source.page || '-'}`}</span>
                        </div>
                        <div className="source-text-box">
                            {source.exact_text || "No exact text snippet available."}
                        </div>

                        <div style={{ marginTop: '16px', color: 'var(--text-muted)', fontSize: '0.8rem' }}>
                            Clause: {source.clause && source.clause !== 'NOT_FOUND' ? source.clause : '-'}
                        </div>
                        <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem', marginTop: '4px' }}>
                            Confidence: {confidenceValue}
                        </div>
                    </>
                ) : (
                    <div className="source-text-box" style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>
                        Source tracing metadata is missing for this extracted item.
                    </div>
                )}

                <h4 style={{ fontSize: '0.85rem', textTransform: 'uppercase', color: 'var(--text-muted)', marginTop: '20px', marginBottom: '10px' }}>
                    Parameter Map
                </h4>
                {entries.length === 0 ? (
                    <div className="source-text-box" style={{ marginTop: 0, color: 'var(--text-muted)' }}>
                        No structured parameters were extracted for this component.
                    </div>
                ) : (
                    <div style={{ border: '1px solid var(--hairline)', borderRadius: '12px', overflow: 'hidden' }}>
                        {entries.map(([key, value]) => (
                            <div key={key} style={{ display: 'flex', borderBottom: '1px solid var(--hairline)' }}>
                                <div style={{ width: '38%', padding: '10px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', fontSize: '0.75rem', background: 'var(--canvas-soft)' }}>
                                    {key}
                                </div>
                                <div style={{ width: '62%', padding: '10px', fontSize: '0.82rem', color: 'var(--text-body)' }}>
                                    {value}
                                </div>
                            </div>
                        ))}
                    </div>
                )}

                {source?.chunk_id && evidenceList.length === 0 && (
                    <div style={{ marginTop: '20px', fontSize: '0.75rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                        Ref ID: {source.chunk_id}
                    </div>
                )}
            </div>
        </div>
    );
}
