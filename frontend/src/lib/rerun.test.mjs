import test from 'node:test';
import assert from 'node:assert/strict';

import {
  clearRerunning,
  isRerunning,
  markRerunning,
  supersedeJobs,
} from './rerun.js';

test('rerun state is tracked per job', () => {
  let state = {};
  state = markRerunning(state, 'job-a');
  assert.equal(isRerunning(state, 'job-a'), true);
  assert.equal(isRerunning(state, 'job-b'), false);

  state = markRerunning(state, 'job-b');
  assert.equal(isRerunning(state, 'job-a'), true);
  assert.equal(isRerunning(state, 'job-b'), true);

  state = clearRerunning(state, 'job-a');
  assert.equal(isRerunning(state, 'job-a'), false);
  assert.equal(isRerunning(state, 'job-b'), true);
});

test('rerun state never mutates the previous object', () => {
  const before = Object.freeze({ 'job-a': true });
  const after = clearRerunning(before, 'job-a');
  assert.deepEqual(before, { 'job-a': true });
  assert.deepEqual(after, {});
});

test('missing job id is a no-op', () => {
  const state = {};
  assert.equal(markRerunning(state, undefined), state);
  assert.equal(isRerunning(state, ''), false);
});

test('supersedeJobs prepends the new job and marks the original', () => {
  const jobs = [
    { job_id: 'old', filename: 'tender.pdf' },
    { job_id: 'other', filename: 'other.pdf' },
  ];
  const next = supersedeJobs(jobs, 'old', { job_id: 'new', filename: 'tender.pdf' });

  assert.deepEqual(next.map((job) => job.job_id), ['new', 'old', 'other']);
  assert.equal(next[1].superseded_by, 'new');
  assert.equal(next[2].superseded_by, undefined);
  assert.equal(jobs[0].superseded_by, undefined, 'input list is not mutated');
});

test('supersedeJobs does not duplicate an existing new job', () => {
  const jobs = [
    { job_id: 'new', filename: 'tender.pdf' },
    { job_id: 'old', filename: 'tender.pdf' },
  ];
  const next = supersedeJobs(jobs, 'old', { job_id: 'new', filename: 'tender.pdf' });

  assert.deepEqual(next.map((job) => job.job_id), ['new', 'old']);
  assert.equal(next[1].superseded_by, 'new');
});
