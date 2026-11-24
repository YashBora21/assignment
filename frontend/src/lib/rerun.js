/**
 * Re-run state helpers.
 *
 * A re-run is per job: starting one tender's re-extraction must not disable
 * the button on every other card.  These helpers keep that state pure so the
 * behaviour is testable without React.
 */

export function markRerunning(state, jobId) {
  if (!jobId) return state || {};
  return { ...(state || {}), [jobId]: true };
}

export function clearRerunning(state, jobId) {
  const next = { ...(state || {}) };
  delete next[jobId];
  return next;
}

export function isRerunning(state, jobId) {
  return Boolean(jobId && state && state[jobId]);
}

/**
 * Prepend the new job and mark the original as superseded.
 * The new job is never duplicated if the list already contains it.
 */
export function supersedeJobs(jobs, oldJobId, newJob) {
  const list = Array.isArray(jobs) ? jobs : [];
  if (!newJob || !newJob.job_id) return list;
  return [
    newJob,
    ...list
      .filter((job) => job && job.job_id !== newJob.job_id)
      .map((job) => (job.job_id === oldJobId ? { ...job, superseded_by: newJob.job_id } : job)),
  ];
}
