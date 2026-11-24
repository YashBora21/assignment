import React from 'react';
import { ArrowUpDown, Filter, Search, FileText, Clock, Award, ChevronRight } from 'lucide-react';
import { isRerunning } from '../lib/rerun';

export default function Dashboard({ jobs, onSelectJob, sortBy, onSortChange, onRerun, rerunningJobs }) {
  return (
    <div className="dashboard-container">
      <div className="dashboard-header">
        <div>
          <h1>Your Workspace</h1>
          <p>Manage and analyze your tender documents</p>
        </div>
        
        <div className="dashboard-controls">
          <div className="dashboard-search">
            <Search size={18} />
            <input type="text" placeholder="Filter documents by name or content..." />
          </div>
          
          <div className="dashboard-filters">
            <span className="filter-label">Sort by</span>
            <div className="filter-toggle">
              <button 
                className={sortBy === 'recency' ? 'active' : ''}
                onClick={() => onSortChange('recency')}
              >
                Date
              </button>
              <button 
                className={sortBy === 'score' ? 'active' : ''}
                onClick={() => onSortChange('score')}
              >
                Match
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="jobs-grid">
        {jobs.length === 0 ? (
          <div className="empty-grid-state">
            <div className="empty-icon">
              <FileText size={48} />
            </div>
            <h3>No tenders analyzed yet</h3>
            <p>Upload a tender document (PDF, DOCX, JPG or PNG) to start the extraction pipeline.</p>
          </div>
        ) : (
          jobs.map((job, idx) => {
            const processingState = job.status === 'done' ? 'Analysis Complete' : job.status === 'running' ? `Running (${job.progress || 0}%)` : (job.stage || job.status || 'Queued');
            const statusClass = job.status === 'error' ? 'error' : job.status === 'running' ? 'running' : 'ready';
            const reqCount = job.requirements_count ?? job.specs_count ?? 0;
            const itemCount = job.items_count ?? 0;
            const manufacturer = job.match_summary?.best_manufacturer;
            const score = manufacturer ? job.match_summary.best_score : job.match_score;
            const scoreLabel = Number.isFinite(score) && score !== null
              ? (manufacturer ? `${manufacturer} · ${Math.round(score)}%` : `${Math.round(score)}%`)
              : '—';
            const jobRerunning = isRerunning(rerunningJobs, job.job_id);
            const supersedingJob = job.superseded_by
              ? jobs.find((entry) => entry.job_id === job.superseded_by)
              : null;
            return (
            <div 
              key={job.job_id} 
              className={`dashboard-card ${statusClass} animate-fade`}
              style={{ animationDelay: `${idx * 0.05}s` }}
              onClick={() => onSelectJob(job)}
            >
              <div className="card-header">
                <div className="card-type-icon">
                  <FileText size={20} />
                </div>
                <div className={`status-badge ${statusClass}`}>
                  {processingState}
                </div>
              </div>
              
              <div className="card-body">
                <h3 className="card-title">{job.filename}</h3>
                <p className="card-msg">{job.message}</p>
                
                <div style={{ display: 'flex', gap: '16px', marginTop: '10px', fontSize: '13px', color: 'var(--text-muted)' }}>
                  <span><strong>{reqCount}</strong> reqs</span>
                  <span><strong>{itemCount}</strong> items</span>
                  {job.superseded_by && (
                    <span
                      role="link"
                      tabIndex={0}
                      title="Open the newer analysis of this tender"
                      style={{ color: 'var(--amber-dark)', cursor: 'pointer', textDecoration: 'underline' }}
                      onClick={(event) => {
                        event.stopPropagation();
                        if (supersedingJob) onSelectJob(supersedingJob);
                      }}
                    >
                      Superseded — view newer
                    </span>
                  )}
                </div>

                {job.status === 'running' && (
                  <div className="card-progress">
                    <div className="progress-bar">
                      <div className="progress-fill" style={{ width: `${job.progress}%` }}></div>
                    </div>
                    <span className="progress-text">{job.progress}%</span>
                  </div>
                )}
              </div>

              <div className="card-footer">
                <div className="card-score">
                  <Award size={14} />
                  <span>Match: {scoreLabel}</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  {onRerun && (job.status === 'done' || job.status === 'error') && (
                    <button
                      className="outline-pill"
                      disabled={jobRerunning}
                      onClick={(event) => { event.stopPropagation(); onRerun(job); }}
                      style={{ fontSize: '12px', padding: '4px 10px' }}
                    >
                      {jobRerunning ? 'Re-extracting...' : 'Re-extract'}
                    </button>
                  )}
                  <div className="card-action">
                    <span>View</span>
                    <ChevronRight size={14} />
                  </div>
                </div>
              </div>
            </div>
            );
          })
        )}
      </div>
    </div>
  );
}
