import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { XCircle, ArrowLeft, Download, AlertTriangle, RefreshCw } from 'lucide-react';
import SpecsTable from './SpecsTable';
import ScopePanel from './ScopePanel';
import ChatPanel from './ChatPanel';
import ScorePanel from './ScorePanel';
import MatchMatrix from './MatchMatrix';
import ReconciliationPanel from './ReconciliationPanel';
import ReviewNotesPanel from './ReviewNotesPanel';
import CostTokenWidget from './CostTokenWidget';

const api = axios.create({ baseURL: '/api' });

function confidenceToNumber(raw) {
  if (typeof raw === 'number') {
    return Math.max(0, Math.min(1, raw));
  }
  const normalized = String(raw || '').toUpperCase();
  if (normalized === 'HIGH') return 0.9;
  if (normalized === 'MEDIUM') return 0.65;
  if (normalized === 'LOW') return 0.35;
  return 0.0;
}

function normalizeSpecs(items) {
  return (Array.isArray(items) ? items : []).map((spec, index) => {
    const component = spec.component || spec.item_name || `Spec ${index + 1}`;

    let specsMap = {};
    if (spec.specs && typeof spec.specs === 'object' && !Array.isArray(spec.specs)) {
      specsMap = spec.specs;
    } else {
      const legacy = {
        specification_text: spec.specification_text,
        unit: spec.unit,
        numeric_value: spec.numeric_value,
        tolerance: spec.tolerance,
        standard_reference: spec.standard_reference,
        material: spec.material,
      };
      Object.entries(legacy).forEach(([key, value]) => {
        if (value && value !== 'NOT_FOUND') {
          specsMap[key] = String(value);
        }
      });
    }

    return {
      component,
      boq_item_id: spec.boq_item_id || '',
      specs: specsMap,
      confidence: confidenceToNumber(spec.confidence),
      source: {
        chunk_id: spec?.source?.chunk_id || 'NOT_FOUND',
        page: spec?.source?.page || 0,
        document: spec?.source?.document || '',
        row: spec?.source?.row ?? null,
        clause: spec?.source?.clause || 'NOT_FOUND',
        exact_text: spec?.source?.exact_text || 'NOT_FOUND',
      },
    };
  });
}

function normalizeScope(scope) {
  const base = scope || {};

  const legacyTasks = Array.isArray(base.tasks) ? base.tasks : [];
  const inferredDeliverables = legacyTasks
    .map((task) => task?.task_description)
    .filter(Boolean);

  return {
    summary: base.summary || 'NOT_FOUND',
    deliverables: Array.isArray(base.deliverables) && base.deliverables.length > 0
      ? base.deliverables
      : inferredDeliverables,
    exclusions: Array.isArray(base.exclusions)
      ? base.exclusions.map((entry) => (typeof entry === 'string' ? entry : entry?.item)).filter(Boolean)
      : [],
    locations: Array.isArray(base.locations) ? base.locations : [],
    references: Array.isArray(base.references) ? base.references : [],
  };
}

export default function ResultViewer({ job, onBack, onRerun, rerunning }) {
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('match');
  const [error, setError] = useState(null);
  // Chat history is lifted here so it survives switching between tabs
  const [chatMessages, setChatMessages] = useState([]);
  const jobId = job?.job_id || null;
  const jobStatus = job?.status || null;

  useEffect(() => {
    let cancelled = false;

    const loadResult = async () => {
      if (!jobId || jobStatus !== 'done') {
        setResult(null);
        setError(null);
        return;
      }

      setLoading(true);
      setError(null);
      try {
        const res = await api.get(`/jobs/${jobId}/result`);
        if (!cancelled) {
          setResult(res.data);
        }
      } catch (err) {
        if (!cancelled) {
          console.error(err);
          setError('Failed to load extracted result JSON.');
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    loadResult();
    return () => {
      cancelled = true;
    };
  }, [jobId, jobStatus]);

  const specs = useMemo(
    () => normalizeSpecs(result?.technical_specifications || []),
    [result],
  );

  const scope = useMemo(
    () => normalizeScope(result?.scope_of_work || {}),
    [result],
  );

  const groundingScore = Number(
    result?.grounding_score ?? result?.accuracy_score ?? 0,
  );

  const reconciliation = result?.reconciliation || {};
  const conflictCount = reconciliation?.conflicts?.length || 0;

  const handleDownload = () => {
    if (!result) return;
    const blob = new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    const base = (job?.filename || 'tender').replace(/\.[^.]+$/, '');
    link.download = `${base}_extraction.json`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  if (!job) return null;

  const progress = Number(job.progress || 0);

  if (job.status !== 'done') {
    return (
      <div className="empty-state">
        <div style={{ textAlign: 'center' }}>
          {job.status === 'error' ? (
            <>
              <XCircle size={48} className="upload-icon mx-auto mb-4" color="var(--status-red)" />
              <h3>Extraction failed</h3>
              <p className="job-message" style={{ marginTop: '10px' }}>{job.message}</p>
              <button className="outline-pill" onClick={onBack} style={{ marginTop: '24px' }}>Back to Workspace</button>
            </>
          ) : (
            <>
              <div style={{ marginBottom: '20px' }}>
                <span className={`badge ${job.status}`}>{job.status}</span>
              </div>
              <h3>Processing document</h3>
              <p className="job-message" style={{ marginTop: '10px', maxWidth: '640px' }}>{job.message}</p>
              <div className="progress-track" style={{ marginTop: '20px', width: '420px', height: '8px' }}>
                <div className="progress-fill" style={{ width: `${progress}%`, transition: 'width 0.5s ease' }} />
              </div>
              <button className="outline-pill" onClick={onBack} style={{ marginTop: '32px' }}>Minimize Analysis</button>
            </>
          )}
        </div>
      </div>
    );
  }

  if (loading) return <div className="empty-state">Loading results...</div>;
  if (error) return <div className="empty-state" style={{ color: 'var(--status-red)' }}>{error}</div>;
  if (!result) return <div className="empty-state">No result payload found.</div>;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <div className="result-header" style={{ padding: '24px 40px', background: 'var(--surface-card)', borderBottom: '1px solid var(--hairline)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
           <button className="icon-btn" onClick={onBack} title="Back to Workspace">
             <ArrowLeft size={20} />
           </button>
           <div className="result-title">
             <h2 style={{ fontSize: '24px', fontWeight: 600, marginBottom: '4px', letterSpacing: '-0.02em' }}>{job.filename}</h2>
             <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span className={`badge ${job.status === 'done' ? 'ready' : job.status}`}>
                  {job.status === 'done' ? 'Analysis Complete' : (job.stage || job.status || 'Processing')}
                </span>
                {job.superseded_by && (
                  <span className="badge warning" style={{ cursor: 'pointer' }}>
                    Superseded by {job.superseded_by.slice(0, 8)}
                  </span>
                )}
                <span style={{ fontSize: '12px', color: 'var(--text-muted-soft)' }}>
                  Analyzed {new Date(job.created_at * 1000).toLocaleDateString()}
                </span>
             </div>
           </div>
        </div>
        
        <div style={{ display: 'flex', alignItems: 'center', gap: '24px' }}>
          <div className="result-stats">
            <div className="result-stats-item">
              <span>Requirements</span>
              <strong>{result?.canonical_requirements?.length || specs.length}</strong>
            </div>
            <div className="result-stats-item">
              <span>Items</span>
              <strong>{result?.procurement_items?.length || 0}</strong>
            </div>
            <div className="result-stats-item">
              <span>Deliverables</span>
              <strong>{scope.deliverables.length}</strong>
            </div>
            <div className="result-stats-item">
              <span>Grounding</span>
              <strong>{groundingScore > 0 ? `${groundingScore.toFixed(1)}%` : '—'}</strong>
            </div>
          </div>
          <div className="v-divider"></div>
          <div style={{ display: 'flex', gap: '8px' }}>
             {onRerun && (
               <button
                 className="outline-pill"
                 disabled={rerunning}
                 onClick={() => onRerun(job)}
                 style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}
                 title="Re-extract tender documents"
               >
                 <RefreshCw size={14} className={rerunning ? 'spin' : ''} />
                 {rerunning ? 'Re-extracting...' : 'Re-extract'}
               </button>
             )}
             <button className="icon-btn" title="Download extraction JSON" onClick={handleDownload}>
               <Download size={18} />
             </button>
          </div>
        </div>
      </div>

      <ReviewNotesPanel result={result} reconciliation={reconciliation} />

      <CostTokenWidget result={result} matchReport={job?.match_summary || job?.match_data} />

      <div className="tabs">
        <div className={`tab ${activeTab === 'specs' ? 'active' : ''}`} onClick={() => setActiveTab('specs')}>
          Specifications
        </div>
        <div className={`tab ${activeTab === 'scope' ? 'active' : ''}`} onClick={() => setActiveTab('scope')}>
          Scope of Work
        </div>
        <div className={`tab ${activeTab === 'qa' ? 'active' : ''}`} onClick={() => setActiveTab('qa')}>
          Ask Document
        </div>
        <div className={`tab ${activeTab === 'reconcile' ? 'active' : ''}`} onClick={() => setActiveTab('reconcile')}>
          Reconciliation{conflictCount ? ` (${conflictCount})` : ''}
        </div>
        <div className={`tab ${activeTab === 'match' ? 'active' : ''}`} onClick={() => setActiveTab('match')}>
          Manufacturer Match
        </div>
        <div className={`tab ${activeTab === 'score' ? 'active' : ''}`} onClick={() => setActiveTab('score')}>
          Company Fit
        </div>
      </div>

      <div style={{ flex: 1, overflow: 'hidden', background: 'var(--canvas)', position: 'relative' }}>
        {/* Always keep panels mounted — CSS visibility prevents unmounting during in-flight requests */}
        <div style={{ display: activeTab === 'specs' ? 'block' : 'none', height: '100%', overflow: 'auto' }}>
          <SpecsTable specs={specs} />
        </div>
        <div style={{ display: activeTab === 'scope' ? 'block' : 'none', height: '100%', overflow: 'auto' }}>
          <ScopePanel scope={scope} procurementItems={result.procurement_items || []} />
        </div>
        <div style={{ display: activeTab === 'qa' ? 'flex' : 'none', flexDirection: 'column', height: '100%' }}>
          <ChatPanel
            job={job}
            messages={chatMessages}
            setMessages={setChatMessages}
          />
        </div>
        <div style={{ display: activeTab === 'reconcile' ? 'block' : 'none', height: '100%', overflow: 'auto' }}>
          <ReconciliationPanel reconciliation={reconciliation} />
        </div>
        <div style={{ display: activeTab === 'match' ? 'block' : 'none', height: '100%', overflow: 'auto' }}>
          <MatchMatrix
            jobId={job.job_id}
            tenderResult={result}
          />
        </div>
        <div style={{ display: activeTab === 'score' ? 'block' : 'none', height: '100%', overflow: 'auto' }}>
          <ScorePanel jobId={job.job_id} initialData={job.match_data} />
        </div>
      </div>
    </div>
  );
}
