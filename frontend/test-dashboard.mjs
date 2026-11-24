import assert from 'node:assert/strict';
import { test, after } from 'node:test';
import { createServer } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const server = await createServer({ root: new URL('.', import.meta.url).pathname.replace(/^\/(\w:)/, '$1'), server: { middlewareMode: true }, appType: 'custom' });
after(() => server.close());
const { default: Dashboard } = await server.ssrLoadModule('/src/components/Dashboard.jsx');
const render = (fields) => renderToStaticMarkup(React.createElement(Dashboard, {
  jobs: [{ job_id: 'test', status: 'done', filename: 'tender.pdf', created_at: 1, ...fields }],
  onSelectJob() {}, onSortChange() {}, onRerun() {},
}));

for (const score of [null, undefined, NaN, Infinity]) {
  test(`missing score ${score} renders an em dash`, () => {
    const html = render({ match_score: score });
    assert.match(html, /—/);
    assert.doesNotMatch(html, /(?:null|undefined|NaN|Infinity)%/);
  });
}
test('zero is a real score', () => assert.match(render({ match_score: 0 }), /0% Company Fit/));
test('missing manufacturer score is not zero', () => assert.match(render({ match_summary: { best_manufacturer: 'Vendor', best_score: null } }), /—/));
test('incomplete results are blocked, not green done', () => {
  const html = render({ tender_complete: false, match_score: 90 });
  assert.match(html, /Matching blocked/);
  assert.doesNotMatch(html, /status-badge done|90%/);
});
test('warnings require review', () => assert.match(render({ warnings: ['Review source'] }), /Needs review/));
test('stale results offer re-run and hide old scores', () => {
  const html = render({ stale: true, match_score: 90 });
  assert.match(html, /Stale result/);
  assert.match(html, /Re-run extraction/);
  assert.doesNotMatch(html, /status-badge done|90%/);
});
