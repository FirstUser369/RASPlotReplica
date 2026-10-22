// Pass the directory containing tsc's CommonJS output as the first argument.
const assert = require('node:assert/strict');
const path = require('node:path');
globalThis.crypto = require('node:crypto').webcrypto;
const root = path.resolve(process.argv[2]);
const { finiteElevation, profileSegments } = require(path.join(root, 'engineering.js'));
const context = require(path.join(root, 'api/context.js'));
const { api } = require(path.join(root, 'api/client.js'));

(async () => {
  assert.deepEqual([null, undefined, NaN, Infinity, -Infinity, '0'].filter(finiteElevation), []);
  assert.deepEqual([0, -5, 12].filter(finiteElevation), [0, -5, 12]);
  assert.deepEqual(profileSegments([[0, 1], [1, 2], [2, null], [3, 4], [4, 5]]), [[[0, 1], [1, 2]], [[3, 4], [4, 5]]]);
  assert.deepEqual(profileSegments([[0, null], [1, NaN], [2, undefined]]), []);
  assert.deepEqual(profileSegments([[0, 0], [1, Infinity], [2, 2]]), [[[0, 0]], [[2, 2]]]);
  const requests = [];
  globalThis.fetch = (url, options) => new Promise((resolve, reject) => requests.push({ url, options, resolve, reject }));
  const older = api.getProfile('session', 0, ['id-old']);
  const olderRejected = assert.rejects(older, context.StaleResponse);
  context.advanceContext();
  const newer = api.getProfile('session', 0, ['id-new']);
  requests[1].resolve(new Response(JSON.stringify({ profile: 'id-new' })));
  assert.equal((await newer).profile, 'id-new');
  requests[0].resolve(new Response(JSON.stringify({ profile: 'id-old' })));
  await olderRejected;
  assert.notEqual(requests[0].options.headers['X-Rasplot-Context'], requests[1].options.headers['X-Rasplot-Context']);
  const failed = api.getDb('old-session');
  const failedRejected = assert.rejects(failed, context.StaleResponse);
  context.advanceContext(); requests[2].reject(new Error('old network failure'));
  await failedRejected;
  context.setContextReach(3);
  const edit = api.patchWorking('session', [{ id: 1, profiles: { 'stable-id': null } }]);
  assert.equal(requests[3].options.headers['X-Rasplot-Reach'], '3');
  assert.deepEqual(JSON.parse(requests[3].options.body), [{ id: 1, profiles: { 'stable-id': null } }]);
  requests[3].resolve(new Response('{}')); await edit;
  const tick = () => new Promise(resolve => setImmediate(resolve));
  // Same-context edits are serialized; a queued obsolete edit is never sent.
  const firstEdit = api.patchWorking('session', [{id:1, label_text:'first'}]);
  const firstRejected = assert.rejects(firstEdit, context.StaleResponse);
  const count = requests.length;
  const queued = api.patchWorking('session', [{id:1, label_text:'obsolete'}]);
  const queuedRejected = assert.rejects(queued, context.StaleResponse);
  assert.equal(requests.length, count);
  context.advanceContext();
  requests[count - 1].resolve(new Response('{}'));
  await firstRejected; await queuedRejected; await tick();
  assert.equal(requests.length, count);
  // Latest profile read also wins when project context did not change.
  const oldRead = api.getProfile('session', 3, ['same']);
  const oldReadRejected = assert.rejects(oldRead, context.StaleResponse);
  const newRead = api.getProfile('session', 3, ['same']);
  requests.at(-1).resolve(new Response('{"profile":"current"}'));
  assert.equal((await newRead).profile, 'current');
  requests.at(-2).resolve(new Response('{"profile":"obsolete"}'));
  await oldReadRejected;
  // Error category remains available without sacrificing the actionable message.
  const invalid = api.getDb('session');
  const invalidRejected = assert.rejects(invalid, error => error.code === 'STALE_STATE' && error.status === 409 && error.message === 'Review panels');
  requests.at(-1).resolve(new Response('{"code":"STALE_STATE","detail":"Review panels"}', {status:409}));
  await invalidRejected;
  // Failed source creation cannot return a previous session as success.
  const opening = api.openSource('missing.p04.hdf');
  const openingRejected = assert.rejects(opening, error => error.code === 'FILE_ACCESS');
  requests.at(-1).resolve(new Response('{"code":"FILE_ACCESS","detail":"File missing"}', {status:404}));
  await openingRejected;
  console.log('PASS: 13 frontend contracts (baseline 9 plus mutation queue, latest profile read, categorized errors, failed source opening)');
})().catch(error => { console.error(error); process.exitCode = 1; });
