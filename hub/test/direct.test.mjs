import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { build } from 'esbuild';
import { Miniflare, Headers as MFHeaders } from 'miniflare';
import { putImmutableObject, sha256 } from '../src/r2-objects.ts';

const root = new URL('./fixtures/release-v2/', import.meta.url);
const inventoryText = await readFile(new URL('inventory.json', root), 'utf8');
const inventory = JSON.parse(inventoryText);
const recall = JSON.parse(await readFile(new URL('recalls.json', root), 'utf8'));
const identity = { commit: inventory.commit, release: inventory.release, inventory_digest: await sha256(inventoryText) };
const token = 'r'.repeat(43);
const hash = (await sha256(token)).slice(7);

async function worker(t, audience = 'public', intake = 'true') {
  const bundled = await build({ entryPoints: [new URL('../src/index.ts', import.meta.url).pathname], bundle: true, format: 'esm', platform: 'browser', write: false, loader: { '.json': 'json' } });
  const runtime = new Miniflare({ telemetry: { enabled: false }, workers: [{ config: {
    type: 'worker', name: 'hub-test', compatibilityDate: '2026-09-03',
    manifest: { mainModule: 'hub.mjs', modules: { 'hub.mjs': { type: 'esm', contents: bundled.outputFiles[0].text } } },
    env: { HUB_DB: { type: 'd1', name: 'HUB_DB' }, SKILLS_BUCKET: { type: 'r2', name: 'SKILLS_BUCKET' },
      HUB_AUDIENCE: { type: 'text', value: audience }, HUB_ORG: { type: 'text', value: 'public' },
      HUB_INTAKE_ENABLED: { type: 'text', value: intake }, HUB_MONTHLY_CAP_MICROUSD: { type: 'text', value: '10000000' } }, exports: {},
  } }] });
  t.after(() => runtime.dispose());
  const db = await runtime.getD1Database('HUB_DB');
  for (const statement of (await readFile(new URL('../migrations/0001_intake.sql', import.meta.url), 'utf8')).split(';').map(s => s.trim()).filter(Boolean)) await db.prepare(statement).run();
  await db.prepare('INSERT INTO tokens(hash,org,scope,expires,lineage_id,created) VALUES(?,?,?,?,?,?)').bind(hash, 'public', 'read', 4000000000, 'reader-lineage', 1).run();
  const raw = await runtime.getR2Bucket('SKILLS_BUCKET');
  const bucket = { get: (...a) => raw.get(...a), put: (k,v,o) => raw.put(k,v,o?.onlyIf instanceof Headers ? { ...o, onlyIf: new MFHeaders(o.onlyIf) } : o) };
  for (const file of inventory.files) await putImmutableObject(bucket, inventory.commit, file.path, Uint8Array.from(await readFile(new URL('objects/' + file.path, root))).buffer);
  await putImmutableObject(bucket, inventory.commit, 'inventory.json', new TextEncoder().encode(inventoryText).buffer);
  await putImmutableObject(bucket, inventory.commit, 'complete.json', new TextEncoder().encode(JSON.stringify(identity)).buffer);
  await bucket.put('current.json', JSON.stringify({ ...identity, revision: 1, sequence: 1, high_water: { commit: identity.commit, sequence: 1 }, previous: null, operation: 'promote', activated_at: '2026-09-25T00:00:00Z' }));
  await bucket.put('recalls/current.json', JSON.stringify(recall));
  const fetcher = await runtime.getWorker('hub-test');
  const get = (path, authorization = token) => fetcher.fetch(`https://hub.example${path}`, { headers: { authorization: `Bearer ${authorization}` } });
  const rpc = (method, params = {}, authorization = token) => fetcher.fetch('https://hub.example/mcp', { method: 'POST', headers: { authorization: `Bearer ${authorization}`, 'content-type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) });
  const modernRpc = (method) => fetcher.fetch('https://hub.example/mcp', { method: 'POST', headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json', accept: 'application/json, text/event-stream', 'mcp-protocol-version': '2026-07-28', 'mcp-method': method }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params: { _meta: { 'io.modelcontextprotocol/protocolVersion': '2026-07-28', 'io.modelcontextprotocol/clientCapabilities': {} } } }) });
  return { get, rpc, modernRpc, db, bucket, post: (path, body, contentType = 'application/json') => fetcher.fetch(`https://hub.example${path}`, { method: 'POST', headers: { authorization: `Bearer ${token}`, 'content-type': contentType }, body: JSON.stringify(body) }) };
}

test('HTTP sync defaults to stable, opts trial in, and excludes recalled revision while preserving independent original note', async t => {
  const { get, rpc } = await worker(t);
  const defaultSync = await get('/sync');
  assert.equal(defaultSync.status, 200);
  const stable = await defaultSync.json();
  assert.equal(stable.audience, 'public');
  assert.deepEqual(stable.notes.map(n => n.note_id), ['service/anchor']);
  assert.match(stable.notes[0].text, /anchor content/);
  assert.match(stable.notes_digest, /^sha256:[0-9a-f]{64}$/);
  const opted = await (await get('/sync?include_trial=true')).json();
  assert.deepEqual(opted.notes.map(n => n.note_id), ['service/anchor', 'service/candidate']);
  const listed = await (await rpc('skills/list')).json();
  assert.deepEqual(listed.result.skills, []);
  const trial = await (await rpc('skills/list', { _meta: { 'io.portwright/include_trial': true } })).json();
  assert.deepEqual(trial.result.skills.map(s => s.grade), ['trial']);
  const unavailable = await rpc('resources/read', { uri: 'skill://gisul/commons/stable/example/SKILL.md' });
  assert.equal(unavailable.status, 200);
  assert.equal((await unavailable.json()).error.code, -32602);
  assert.equal((await get(`/sync?commit=${'a'.repeat(40)}`)).status, 400);
  assert.equal((await get('/sync', 'wrong')).status, 401);
});

test('a newly recalled revision disappears from current and pinned MCP reads and sync', async t => {
  const { get, rpc, bucket } = await worker(t);
  const opted = { _meta: { 'io.portwright/include_trial': true, 'io.gisul/commit': inventory.commit } };
  const before = await (await rpc('skills/list', opted)).json();
  assert.deepEqual(before.result.skills.map(s => s.grade), ['trial']);
  const candidate = inventory.skills[1].note_refs[0];
  await bucket.put('recalls/current.json', JSON.stringify({ ...recall, sequence: recall.sequence + 1, entries: [...recall.entries, candidate] }));
  const after = await (await rpc('skills/list', opted)).json();
  assert.deepEqual(after.result.skills, []);
  const synced = await (await get('/sync?include_trial=true')).json();
  assert.deepEqual(synced.notes.map(n => n.note_id), ['service/anchor']);
  const read = await (await rpc('resources/read', { uri: inventory.skills[1].uri, ...opted })).json();
  assert.equal(read.error.code, -32602);
});

test('personal cannot enable intake; valid personal reader has no submit tools', async t => {
  const broken = await worker(t, 'personal', 'true');
  assert.equal((await broken.get('/healthz')).status, 503);
  const personal = await worker(t, 'personal', 'false');
  assert.deepEqual((await (await personal.rpc('tools/list')).json()).result.tools, []);
  assert.equal((await personal.get('/sync')).status, 404);
});

test('personal v1 catalog reads with a D1-registered bearer after URL cutover', async t => {
  const { rpc, bucket } = await worker(t, 'personal', 'false');
  const commit = 'c'.repeat(40), uri = 'skill://gisul/personal/demo/SKILL.md';
  const text = '---\nname: demo\ndescription: Private demo\n---\n\nPrivate\n';
  const file = await putImmutableObject(bucket, commit, 'personal/demo/SKILL.md', new TextEncoder().encode(text).buffer);
  const v1 = { schema_version: 1, commit, release: 'personal.old', skills: [{ uri, frontmatter: { name: 'demo', description: 'Private demo' }, resources: [{ uri, digest: file.digest, size: file.size }] }], files: [{ path: 'personal/demo/SKILL.md', uri, digest: file.digest, size: file.size }], aliases: {} };
  const data = JSON.stringify(v1);
  const release = { commit, release: v1.release, inventory_digest: await sha256(data) };
  await putImmutableObject(bucket, commit, 'inventory.json', new TextEncoder().encode(data).buffer);
  await bucket.put('current.json', JSON.stringify({ ...release, revision: 2, sequence: 2, high_water: { commit, sequence: 2 }, previous: identity, operation: 'promote', activated_at: '2026-09-25T01:00:00Z' }));
  const list = await (await rpc('skills/list')).json();
  assert.deepEqual(list.result.skills.map(skill => skill.uri), [uri]);
  const read = await (await rpc('resources/read', { uri })).json();
  assert.match(read.result.contents[0].text, /Private/);
});

test('modern MCP discovery and reads carry private zero-TTL cache hints', async t => {
  const { modernRpc } = await worker(t);
  for (const method of ['server/discover', 'skills/list']) {
    const response = await modernRpc(method);
    assert.equal(response.status, 200);
    const data = (await response.json()).result;
    assert.equal(data.ttlMs, 0);
    assert.equal(data.cacheScope, 'private');
  }
});

test('admin requires operator and keeps publisher routes unchanged', async t => {
  const { get, db } = await worker(t);
  assert.equal((await get('/admin/current')).status, 403);
  await db.prepare('UPDATE tokens SET scope=? WHERE hash=?').bind('operator', hash).run();
  const current = await get('/admin/current');
  assert.equal(current.status, 200);
  assert.equal((await current.json()).current.commit, inventory.commit);
  const listed = await get('/admin/intake?state=pending');
  assert.equal(listed.status, 200);
  assert.deepEqual((await listed.json()).items, []);
  assert.equal((await get('/admin/intake?state=pending&limit=20')).status, 200);
  assert.equal((await get('/admin/events?limit=100')).status, 200);
  const projection = await get('/admin/recalls');
  assert.equal(projection.status, 200);
  assert.equal((await projection.json()).snapshot.audience, 'public');
});

test('operator HTTP budget accepts the H3 flat confirmation reservation and settles an unused grant', async t => {
  const { get, post, db } = await worker(t);
  await db.prepare('UPDATE tokens SET scope=? WHERE hash=?').bind('operator', hash).run();
  const request = { action: 'reserve', operation_id: crypto.randomUUID(), purpose: 'confirm',
    note_id: 'service/candidate', revision: `sha256:${'a'.repeat(64)}`, target_digest: `sha256:${'b'.repeat(64)}`,
    reserve_micro_usd: 10000, request_digest: `sha256:${'c'.repeat(64)}`, bytes: 128 };
  const granted = await post('/admin/budget', request);
  assert.equal(granted.status, 200);
  const reservation = await granted.json();
  assert.equal(reservation.granted, true);
  assert.equal((await (await get('/admin/budget')).json()).reserved_micro_usd, 10000);
  const settled = await post('/admin/budget', { action: 'settle', reservation_id: reservation.reservation_id, actual_micro_usd: 0, status: 'unused', usage: {} });
  assert.equal(settled.status, 200);
  assert.equal((await (await get('/admin/budget')).json()).available_micro_usd, 10000000);
});

test('operator can append a recall projection with more than eight hundred safe entries', async t => {
  const { get, db, post } = await worker(t);
  await db.prepare('UPDATE tokens SET scope=? WHERE hash=?').bind('operator', hash).run();
  const current = await (await get('/admin/recalls')).json();
  const entries = Array.from({ length: 817 }, (_, n) => ({ note_id: `service/item-${n}`, revision: `sha256:${'a'.repeat(64)}` }));
  const request = await (await post('/admin/recalls', { snapshot: { ...current.snapshot, sequence: current.snapshot.sequence + 1, entries: [...current.snapshot.entries, ...entries] }, expected_etag: current.etag })).json();
  assert.equal(request.entries.length, current.snapshot.entries.length + 817);
});


test('admin metadata requires JSON and rejects the wrong content type before writing', async t => {
  const { db, post } = await worker(t);
  await db.prepare('UPDATE tokens SET scope=? WHERE hash=?').bind('operator', hash).run();
  const current = await db.prepare('SELECT count(*) AS n FROM tokens').first();
  const response = await post('/admin/tokens', { action: 'issue', scope: 'read', expires: 4000000000 }, 'text/plain');
  assert.equal(response.status, 415);
  assert.equal((await post('/admin/tokens', null)).status, 400);
  assert.equal((await post('/admin/intake', null)).status, 400);
  assert.equal((await db.prepare('SELECT count(*) AS n FROM tokens').first()).n, current.n);
});