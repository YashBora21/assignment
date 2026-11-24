import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    CheckCircle2, AlertTriangle, XCircle, HelpCircle,
    Info, Download, Search, Play, Loader2
} from 'lucide-react';
import SourceDrawer from './SourceDrawer';

const api = axios.create({ baseURL: '/api' });

const STATUS_META = {
    full_match: { label: 'Full Match', color: '#16a34a', icon: CheckCircle2 },
    partial_match: { label: 'Partial', color: '#d97706', icon: AlertTriangle },
    no_match: { label: 'No Match', color: '#dc2626', icon: XCircle },
    no_evidence: { label: 'No Evidence', color: '#64748b', icon: HelpCircle },
    not_applicable: { label: 'N/A', color: '#94a3b8', icon: Info },
    unindexed: { label: 'Unindexed', color: '#dc2626', icon: AlertTriangle },
};

function statusMeta(status) {
    return STATUS_META[status] || { label: status || 'Unknown', color: '#6b7280', icon: Info };
}

function scoreColor(score) {
    if (score >= 80) return '#16a34a';
    if (score >= 50) return '#d97706';
    return '#dc2626';
}

function requirementLabel(req, index) {
    if (!req) return `Requirement ${index + 1}`;
    const bits = [req.parameter, req.value, req.unit]
        .filter((part) => part && part !== 'NOT_FOUND')
        .join(' ');
    return bits || req.text || `Requirement ${index + 1}`;
}

function downloadJson(filename, payload) {
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}

function RankingCard({ manufacturer }) {
    const score = Number(manufacturer.overall_compatibility ?? 0);
    const coverage = Number(manufacturer.coverage ?? 0);
    const confidence = Number(manufacturer.evidence_confidence ?? 0);
    const counts = manufacturer.status_counts || {};
    const isUnindexed = manufacturer.tender_match_status === 'unindexed';
    const isLowCoverage = manufacturer.coverage !== undefined && manufacturer.coverage < 50;

    return (
        <div style={{
            minWidth: '240px', flex: '1 1 240px', background: 'var(--surface-card)',
            border: isUnindexed ? '1px solid #dc2626' : '1px solid var(--hairline)',
            borderRadius: '16px', padding: '18px 20px',
        }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '12px' }}>
                <div style={{ fontWeight: 700, color: 'var(--text-ink)', fontSize: '15px' }}>
                    {!isUnindexed && <span style={{ color: 'var(--text-muted-soft)', marginRight: '6px' }}>#{manufacturer.rank}</span>}
                    {manufacturer.manufacturer}
                </div>
                {isUnindexed ? (
                    <span className="badge error" style={{ fontSize: '11px' }}>Unindexed</span>
                ) : (
                    <div style={{ fontSize: '24px', fontWeight: 600, color: scoreColor(score) }}>
                        {score.toFixed(0)}<span style={{ fontSize: '13px', color: 'var(--text-muted-soft)' }}>%</span>
                    </div>
                )}
            </div>

            {isUnindexed ? (
                <div style={{ marginTop: '8px', fontSize: '12px', color: '#dc2626', lineHeight: 1.4 }}>
                    ⚠️ Category matches but no catalogue content found in index (ingestion error).
                </div>
            ) : (
                <>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', color: 'var(--text-muted)', marginTop: '8px' }}>
                        <span>Compatibility</span>
                        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                            <span>Coverage: {coverage.toFixed(0)}% {isLowCoverage && '(Low)'}</span>
                            {confidence > 0 && (
                                <span title="Trustworthiness of verified citations" style={{ color: confidence >= 75 ? '#16a34a' : 'var(--text-muted)', fontWeight: 500 }}>
                                    · Conf: {confidence.toFixed(0)}%
                                </span>
                            )}
                        </div>
                    </div>
                    <div className="progress-track" style={{ height: '6px', marginTop: '4px', background: 'var(--surface-strong)' }}>
                        <div className="progress-fill" style={{ width: `${Math.max(0, Math.min(100, score))}%`, background: scoreColor(score) }} />
                    </div>
                </>
            )}

            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '12px' }}>
                {Object.entries(counts).map(([status, count]) => {
                    if (count === 0) return null;
                    const meta = statusMeta(status);
                    return (
                        <span key={status} className="badge" style={{
                            fontSize: '10px', padding: '2px 8px', color: meta.color,
                            border: `1px solid ${meta.color}55`, background: `${meta.color}12`,
                        }}>
                            {meta.label}: {count}
                        </span>
                    );
                })}
            </div>
        </div>
    );
}

export default function MatchMatrix({ jobId, tenderResult = null, initialReport = null }) {
    const [report, setReport] = useState(initialReport);
    const [loading, setLoading] = useState(!initialReport);
    const [running, setRunning] = useState(false);
    const [error, setError] = useState(null);
    const [search, setSearch] = useState('');
    const [statusFilter, setStatusFilter] = useState('all');
    const [manufacturerFilter, setManufacturerFilter] = useState('all');
    const [selected, setSelected] = useState(null);

    // Categories come from the backend manifests and the match report; no
    // product-family list is hardcoded in the UI.
    const [allCatalogManufacturers, setAllCatalogManufacturers] = useState([]);
    const [catalogCategories, setCatalogCategories] = useState([]);

    const [activeCategory, setActiveCategory] = useState('all');
    const [selectedVendors, setSelectedVendors] = useState([]);

    // Fetch dynamic manufacturers list from backend
    useEffect(() => {
        let isMounted = true;
        api.get('/manufacturers')
            .then(res => {
                if (isMounted && res.data?.manufacturers) {
                    setAllCatalogManufacturers(res.data.manufacturers);
                    setCatalogCategories(res.data.categories || []);
                }
            })
            .catch(() => {});
        return () => { isMounted = false; };
    }, []);

    const tenderCategories = useMemo(() => {
        const fromReport = report?.categories || [];
        const fromResult = tenderResult?.categories || [];
        const primary = report?.category || tenderResult?.category;
        const merged = [...new Set([...(primary ? [primary] : []), ...fromReport, ...fromResult].filter(Boolean))];
        return merged;
    }, [report, tenderResult]);

    const availableCategories = useMemo(
        () => [...new Set([...catalogCategories, ...tenderCategories])].sort(),
        [catalogCategories, tenderCategories],
    );

    // Set initial category from the tender when the catalogue knows it.
    useEffect(() => {
        if (activeCategory !== 'all') return;
        const first = tenderCategories[0];
        if (first && availableCategories.includes(first)) {
            setActiveCategory(first);
        }
    }, [tenderCategories, availableCategories, activeCategory]);

    // Load existing match report if available
    useEffect(() => {
        if (!jobId) return;
        let cancelled = false;

        const load = async () => {
            setLoading(true);
            setError(null);
            try {
                const res = await api.get(`/jobs/${jobId}/match`);
                const data = res.data || {};
                if (cancelled) return;
                if (data.error) {
                    // Match report not generated yet is normal
                    if (data.error !== 'match report not generated') {
                        setError(data.error);
                    }
                    setReport(null);
                } else {
                    setReport(data);
                }
            } catch {
                if (!cancelled) {
                    setError('Could not load the match report.');
                }
            } finally {
                if (!cancelled) setLoading(false);
            }
        };

        load();
        return () => { cancelled = true; };
    }, [jobId]);

    const availableVendors = useMemo(() => {
        if (!allCatalogManufacturers.length) return [];
        if (activeCategory === 'all') return allCatalogManufacturers;
        return allCatalogManufacturers.filter(
            (m) => (m.categories || [m.category]).filter(Boolean).includes(activeCategory),
        );
    }, [allCatalogManufacturers, activeCategory]);

    const runMatching = async () => {
        if (!jobId || running) return;
        setRunning(true);
        setError(null);
        try {
            const body = {};
            if (selectedVendors.length > 0) {
                body.selected_manufacturers = selectedVendors;
            }
            if (activeCategory !== 'all') {
                body.category_filter = activeCategory;
            }
            const res = await api.post(`/jobs/${jobId}/match`, body);
            const data = res.data || {};
            if (data.error) {
                setError(data.error);
            } else {
                setReport(data);
            }
        } catch (err) {
            setError(err?.response?.data?.detail || err.message || 'Matching request failed.');
        } finally {
            setRunning(false);
        }
    };

    const toggleVendor = (slug) => {
        setSelectedVendors((prev) =>
            prev.includes(slug) ? prev.filter((s) => s !== slug) : [...prev, slug]
        );
    };

    const selectAllVendors = () => {
        setSelectedVendors(availableVendors.map(v => v.slug));
    };

    const clearAllVendors = () => {
        setSelectedVendors([]);
    };

    const requirements = useMemo(() => report?.requirements || [], [report]);
    const manufacturers = useMemo(
        () => [...(report?.manufacturers || [])].sort(
            (a, b) => (a.rank || 999) - (b.rank || 999)
        ),
        [report],
    );

    const visibleManufacturers = useMemo(
        () => (manufacturerFilter === 'all'
            ? manufacturers
            : manufacturers.filter((item) => item.manufacturer === manufacturerFilter)),
        [manufacturers, manufacturerFilter],
    );

    const matchLookup = useMemo(() => {
        const lookup = {};
        manufacturers.forEach((manufacturer) => {
            const perRequirement = {};
            (manufacturer.matches || []).forEach((match) => {
                perRequirement[match.requirement_id] = match;
            });
            lookup[manufacturer.manufacturer] = perRequirement;
        });
        return lookup;
    }, [manufacturers]);

    const visibleRequirements = useMemo(() => {
        const needle = search.trim().toLowerCase();
        return requirements.filter((req) => {
            if (needle) {
                const blob = `${req.text} ${req.parameter} ${req.value} ${req.unit}`.toLowerCase();
                if (!blob.includes(needle)) return false;
            }
            if (statusFilter === 'all') return true;
            return visibleManufacturers.some((manufacturer) => {
                const match = matchLookup[manufacturer.manufacturer]?.[req.requirement_id];
                return match?.status === statusFilter;
            });
        });
    }, [requirements, search, statusFilter, visibleManufacturers, matchLookup]);

    if (loading) {
        return <div className="empty-state">Loading manufacturer match report…</div>;
    }

    if (!report) {
        return (
            <div className="empty-state" style={{ padding: '48px' }}>
                <div style={{ maxWidth: '640px', textAlign: 'center' }}>
                    <Info size={44} style={{ opacity: 0.35, marginBottom: '16px' }} />
                    <h3 style={{ marginBottom: '10px' }}>Category-Scoped Manufacturer Matching</h3>
                    <p style={{ color: 'var(--text-muted)', lineHeight: 1.6, marginBottom: '22px' }}>
                        Select the product category and manufacturer catalogues to evaluate with verified evidence.
                    </p>

                    <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: '8px', marginBottom: '16px' }}>
                        {['all', ...availableCategories].map((cat) => (
                            <button
                                key={cat}
                                className={`badge ${activeCategory === cat ? 'done' : ''}`}
                                style={{
                                    cursor: 'pointer', padding: '6px 14px', fontSize: '13px',
                                    background: activeCategory === cat ? 'var(--primary-ink)' : 'var(--surface-strong)',
                                    color: activeCategory === cat ? '#fff' : 'var(--text-ink)',
                                }}
                                onClick={() => { setActiveCategory(cat); setSelectedVendors([]); }}
                            >
                                {cat.toUpperCase()}
                            </button>
                        ))}
                    </div>

                    <div style={{ display: 'flex', justifyContent: 'center', gap: '12px', marginBottom: '16px', fontSize: '12px' }}>
                        <button className="text-btn" onClick={selectAllVendors}>Select All in Category</button>
                        <span>·</span>
                        <button className="text-btn" onClick={clearAllVendors}>Clear All</button>
                    </div>

                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'center', marginBottom: '28px' }}>
                        {availableVendors.map((v) => {
                            const isChecked = selectedVendors.includes(v.slug);
                            return (
                                <button
                                    key={v.slug}
                                    style={{
                                        cursor: 'pointer', padding: '6px 12px', borderRadius: '8px',
                                        fontSize: '12px', border: isChecked ? '1px solid #16a34a' : '1px solid var(--hairline)',
                                        background: isChecked ? 'rgba(22, 163, 74, 0.12)' : 'var(--surface-card)',
                                        color: isChecked ? '#16a34a' : 'var(--text-ink)',
                                        fontWeight: isChecked ? 600 : 400,
                                    }}
                                    onClick={() => toggleVendor(v.slug)}
                                >
                                    {isChecked ? '✓ ' : '+ '}{v.display_name || v.slug}
                                </button>
                            );
                        })}
                    </div>

                    <button className="primary-pill" onClick={runMatching} disabled={running}>
                        {running ? <Loader2 size={16} className="spin" /> : <Play size={16} />}
                        {running ? 'Running Matching…' : `Run Matching ${selectedVendors.length ? `(${selectedVendors.length} selected)` : 'for Category'}`}
                    </button>
                    {error && (
                        <p style={{ color: 'var(--status-red, #dc2626)', fontSize: '13px', marginTop: '18px' }}>
                            {error}
                        </p>
                    )}
                </div>
            </div>
        );
    }

    const notes = Array.isArray(report.notes) ? report.notes : [];

    return (
        <div style={{ padding: '28px 32px', height: '100%', overflow: 'auto' }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '20px', flexWrap: 'wrap' }}>
                <div>
                    <h2 style={{ margin: 0, fontSize: '24px', fontWeight: 600, letterSpacing: '-0.02em' }}>
                        Manufacturer Match Matrix
                    </h2>
                    <p style={{ color: 'var(--text-muted)', marginTop: '6px', fontSize: '13px' }}>
                        {requirements.length} requirements · {manufacturers.length} manufacturers evaluated
                        {report.category ? ` · ${report.category}` : ''}
                        {report.generated_at ? ` · generated ${new Date(report.generated_at * 1000).toLocaleString()}` : ''}
                    </p>
                </div>
                <div style={{ display: 'flex', gap: '8px' }}>
                    <button className="outline-pill" onClick={runMatching} disabled={running}>
                        {running ? <Loader2 size={14} className="spin" /> : <Play size={14} />}
                        {running ? 'Re-running…' : 'Re-run'}
                    </button>
                    <button
                        className="icon-btn"
                        title="Download match report JSON"
                        onClick={() => downloadJson(`match_${jobId}.json`, report)}
                    >
                        <Download size={18} />
                    </button>
                </div>
            </div>

            {notes.length > 0 && (
                <div style={{
                    marginTop: '18px', padding: '12px 16px', borderRadius: '12px',
                    border: '1px solid #d9770655', background: '#d9770610', color: '#92400e', fontSize: '13px',
                }}>
                    {notes.map((note, index) => <div key={index}>{note}</div>)}
                </div>
            )}

            {(report.skipped_manufacturers || []).length > 0 && (
                <details style={{ marginTop: '14px', fontSize: '12px', color: 'var(--text-muted)' }}>
                    <summary style={{ cursor: 'pointer', fontWeight: 600 }}>
                        {report.skipped_manufacturers.length} manufacturer(s) not evaluated
                    </summary>
                    <ul style={{ margin: '8px 0 0 18px', padding: 0 }}>
                        {report.skipped_manufacturers.map((item, index) => (
                            <li key={`${item.slug || item.manufacturer}-${index}`}>
                                {item.manufacturer || item.slug}: {item.reason}
                            </li>
                        ))}
                    </ul>
                </details>
            )}

            {error && (
                <div style={{ marginTop: '14px', color: 'var(--status-red, #dc2626)', fontSize: '13px' }}>{error}</div>
            )}

            <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', marginTop: '22px' }}>
                {manufacturers.map((manufacturer) => (
                    <RankingCard key={manufacturer.manufacturer} manufacturer={manufacturer} />
                ))}
            </div>

            <div className="controls-bar" style={{ marginTop: '24px', paddingLeft: 0, paddingRight: 0 }}>
                <div className="input-group">
                    <Search size={18} className="input-icon" />
                    <input
                        type="text"
                        className="text-input"
                        placeholder="Filter requirements, parameters or values…"
                        value={search}
                        onChange={(event) => setSearch(event.target.value)}
                    />
                </div>
                <div className="input-group" style={{ background: 'transparent', border: 'none' }}>
                    <select className="select-input" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                        <option value="all">All statuses</option>
                        <option value="full_match">Full match</option>
                        <option value="partial_match">Partial match</option>
                        <option value="no_match">No match</option>
                        <option value="no_evidence">No evidence</option>
                        <option value="not_applicable">Not applicable</option>
                    </select>
                </div>
                <div className="input-group" style={{ background: 'transparent', border: 'none' }}>
                    <select className="select-input" value={manufacturerFilter} onChange={(event) => setManufacturerFilter(event.target.value)}>
                        <option value="all">All manufacturers</option>
                        {manufacturers.map((manufacturer) => (
                            <option key={manufacturer.manufacturer} value={manufacturer.manufacturer}>
                                {manufacturer.manufacturer}
                            </option>
                        ))}
                    </select>
                </div>
                <div style={{ marginLeft: 'auto', alignSelf: 'center', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
                    Showing {visibleRequirements.length} of {requirements.length}
                </div>
            </div>

            <div className="table-container" style={{ marginTop: '8px', maxHeight: 'calc(100vh - 420px)' }}>
                <table>
                    <thead>
                        <tr>
                            <th style={{ position: 'sticky', left: 0, background: 'var(--surface-card)', minWidth: '320px', zIndex: 2 }}>
                                Requirement
                            </th>
                            <th style={{ minWidth: '90px' }}>Source</th>
                            {visibleManufacturers.map((manufacturer) => (
                                <th key={manufacturer.manufacturer} style={{ minWidth: '150px' }}>
                                    <div>{manufacturer.manufacturer}</div>
                                    <div style={{ fontSize: '11px', fontWeight: 400, color: 'var(--text-muted-soft)' }}>
                                        {Number(manufacturer.overall_compatibility || 0).toFixed(0)}% fit · {Number(manufacturer.coverage || 0).toFixed(0)}% cov
                                        {Number(manufacturer.evidence_confidence || 0) > 0 ? ` · ${Number(manufacturer.evidence_confidence).toFixed(0)}% conf` : ''}
                                    </div>
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {visibleRequirements.map((req, reqIndex) => {
                            const sourcePage = req.source?.page ? `p.${req.source.page}` : '';
                            return (
                                <tr key={req.requirement_id || reqIndex}>
                                    <td style={{ position: 'sticky', left: 0, background: 'var(--surface-card)', zIndex: 1 }}>
                                        <div style={{ fontWeight: 600, color: 'var(--text-ink)', fontSize: '13px' }}>
                                            {requirementLabel(req, reqIndex)}
                                        </div>
                                        <div style={{ fontSize: '11px', color: 'var(--text-muted-soft)', marginTop: '2px' }}>
                                            {req.parameter && req.parameter !== 'NOT_FOUND' ? `${req.parameter}: ` : ''}
                                            {req.text}
                                        </div>
                                    </td>
                                    <td style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                                        {sourcePage}
                                    </td>
                                    {visibleManufacturers.map((manufacturer) => {
                                        const match = matchLookup[manufacturer.manufacturer]?.[req.requirement_id];
                                        if (!match) {
                                            return (
                                                <td key={manufacturer.manufacturer} style={{ color: 'var(--text-muted-soft)', fontSize: '12px' }}>
                                                    —
                                                </td>
                                            );
                                        }
                                        const meta = statusMeta(match.status);
                                        const Icon = meta.icon;
                                        return (
                                            <td key={manufacturer.manufacturer}>
                                                <button
                                                    type="button"
                                                    className="badge"
                                                    onClick={() => setSelected({
                                                        requirement: req,
                                                        manufacturer: manufacturer.manufacturer,
                                                        match,
                                                    })}
                                                    title={match.reason || meta.label}
                                                    style={{
                                                        display: 'inline-flex',
                                                        alignItems: 'center',
                                                        gap: '6px',
                                                        border: `1px solid ${meta.color}55`,
                                                        background: `${meta.color}15`,
                                                        color: meta.color,
                                                        cursor: 'pointer',
                                                    }}
                                                >
                                                    <Icon size={12} />
                                                    {meta.label}
                                                </button>
                                                {match.capability && match.capability !== 'NOT_FOUND' && (
                                                    <div style={{ fontSize: '11px', color: 'var(--text-ink)', marginTop: '4px', fontWeight: 500 }}>
                                                        {match.capability}
                                                    </div>
                                                )}
                                                {match.evidence && match.evidence.length > 0 && (
                                                    <div style={{ fontSize: '10px', color: 'var(--text-muted-soft)', marginTop: '2px' }}>
                                                        {match.evidence[0].document} p.{match.evidence[0].page}
                                                    </div>
                                                )}
                                            </td>
                                        );
                                    })}
                                </tr>
                            );
                        })}
                        {visibleRequirements.length === 0 && (
                            <tr>
                                <td colSpan={visibleManufacturers.length + 2} style={{ textAlign: 'center', padding: '32px', color: 'var(--text-muted)' }}>
                                    No requirements match the active filters.
                                </td>
                            </tr>
                        )}
                    </tbody>
                </table>
            </div>

            <SourceDrawer
                isOpen={!!selected}
                onClose={() => setSelected(null)}
                title={selected ? `${selected.manufacturer} · ${requirementLabel(selected.requirement, 0)}` : 'Evidence'}
                source={selected ? {
                    page: selected.match?.evidence?.[0]?.page || selected.requirement?.source?.page || 0,
                    exact_text: selected.match?.evidence?.[0]?.quote || (selected.match?.status === 'no_evidence' ? 'No verbatim quote found in catalogue.' : 'No quote returned.'),
                    clause: selected.match?.capability || 'NOT_FOUND',
                    chunk_id: selected.match?.evidence?.[0]?.chunk_id || 'NOT_FOUND',
                } : null}
                evidence={selected?.match?.evidence || []}
                status={selected?.match?.status || ''}
                reason={selected?.match?.reason || ''}
                specs={{
                    parameter: selected?.requirement?.parameter,
                    value: selected?.requirement?.value,
                    unit: selected?.requirement?.unit,
                    capability: selected?.match?.capability,
                }}
                confidence={selected?.match?.confidence || 0}
            />
        </div>
    );
}
