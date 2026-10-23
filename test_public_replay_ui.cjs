'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = __dirname;
const plan = JSON.parse(fs.readFileSync(path.join(root, 'public_data/coinbase_forward_registration.json'), 'utf8'));
const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, {
    innerHTML: '', textContent: '', disabled: false, listeners: {},
    addEventListener(name, callback) { this.listeners[name] = callback; },
    querySelector(selector) { return element(selector); },
    insertAdjacentHTML(_position, html) { this.innerHTML += html; },
  });
  return elements.get(id);
};
const status = {
  registration: null, publicRegistration: plan, latestEvaluation: null, evaluationCount: 0,
  nowUtc: '2026-10-22T00:00:00+00:00', protocol: plan.protocol,
  dataSources: {marketCachePath: 'C:/cache/coinbase_market.sqlite3',
    localRegistrationPath: 'C:/cache/coinbase_forward/registration.json',
    publicPlanPath: 'C:/repo/public_data/coinbase_forward_registration.json',
    marketAndRegistrationSameDirectory: true},
};
const review = {
  mode: 'public_read_only', evaluatedAtUtc: '2026-10-22T00:00:00+00:00',
  marketAsOfUtc: '2026-10-21T16:00:00+00:00', priceInputSha256: 'a'.repeat(64),
  rawCacheSnapshotSha256: 'b'.repeat(64), scoreImplementationSha256: {'coinbase_forward.py': 'c'.repeat(64)},
  summary: {directionScoredDays: 1, directionFutureDays: 27, directionMissingDays: 2,
    timeScoredDays: 0, timeLateRegistrationDays: 3, timeEventCoveragePct: null,
    allDateWithin3Pct: null, directionRules: [{id: 'DAY-STEM-FIRE-DOWN', n: 1,
      hitRatePct: 100, allDayBaselinePct: 100}], timeRules: []},
  rows: [{date: '2026-10-19', actual: 'down', returnPct: -10,
    directionStatus: 'scored', directionResults: {'DAY-STEM-FIRE-DOWN': 'correct',
      'DAY-BRANCH-YIN-DOWN': 'correct'}, timeStatus: 'late_registration',
    marketEvidence: {status: 'complete', open: '100', high: '101', low: '89', close: '90',
      candleSha256: 'd'.repeat(64), sourceRequestIds: ['request-1'], missingReasons: []}}],
};
const requests = [];
const context = {window: {}, document: {getElementById: element}, Intl, Date, Number,
  Map, Set, String, fetch: async (url, options) => {
    requests.push([url, options]);
    return {ok: true, json: async () => url.endsWith('-status') ? status :
      url.endsWith('-refresh-market') ? {range: {start: '2026-10-14', end: '2026-10-22'}, complete: 9, missing: 0} : review};
  }};
vm.runInNewContext(fs.readFileSync(path.join(root, 'web/coinbase-forward.js'), 'utf8'), context);

(async () => {
  const view = context.window.createCoinbaseForward();
  await view.load();
  assert.equal(element('#cb-forward-public-review').disabled, false);
  assert.equal(element('#cb-forward-evaluate').disabled, true);
  await view.preview();
  assert.deepEqual(requests.map(([url]) => url), [
    '/api/coinbase-forward-status', '/api/coinbase-forward-public-review']);
  assert.equal(requests[1][1].method, undefined);
  assert.match(element('#cb-forward-metrics').innerHTML, /公开计划只读复核/);
  assert.match(element('#cb-forward-schedule').innerHTML, /下跌 -10\.0%/);
  assert.match(element('#cb-forward-schedule').innerHTML, /开 100 · 高 101 · 低 89 · 收 90/);
  assert.match(element('#cb-forward-schedule').innerHTML, /request-1/);
  assert.match(element('#cb-forward-overview').innerHTML, /coinbase_market\.sqlite3/);
  assert.match(element('#cb-forward-overview').innerHTML, /当前评分模块哈希/);
  assert.match(element('#cb-forward-status').textContent, /没有写入登记或评价文件/);
  await view.refreshMarket();
  assert.equal(requests[2][0], '/api/coinbase-forward-refresh-market');
  assert.equal(requests[2][1].method, 'POST');
  assert.equal(requests[3][0], '/api/coinbase-forward-public-review');
  assert.match(element('#cb-forward-status').textContent, /行情更新完成/);
  console.log('public replay UI: OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
