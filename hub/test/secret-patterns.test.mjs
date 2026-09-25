import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { build } from 'esbuild';
import { Miniflare } from 'miniflare';
import { hashToken } from '../src/auth.ts';

const MIGRATION = await readFile(new URL('../migrations/0001_intake.sql', import.meta.url), 'utf8');
const NOW = Math.floor(Date.now() / 1000);
// Match verify_hub.py:assemble; samples are assembled only in memory.
const CORPUS = JSON.parse(await readFile(new URL('../../tests/fixtures/hub/secrets-corpus.json', import.meta.url), 'utf8'));
const assemble = parts => parts.map(part => typeof part === 'string' ? part : part.repeat.repeat(part.count)).join('');
const SAMPLES = CORPUS.cases.filter(item => item.expect === 'reject').map(item => assemble(item.parts));

const bundle = new TextDecoder().decode((await build({
  entryPoints: [new URL('../src/index.ts', import.meta.url).pathname],
  bundle: true, write: false, format: 'esm', target: 'es2022', platform: 'browser',
  conditions: ['workerd', 'worker', 'browser', 'import', 'default'],
})).outputFiles[0].contents);

async function setup(t, script = bundle) {
  const runtime = new Miniflare({
    telemetry: { enabled: false },
    workers: [{
      config: {
        type: 'worker', name: 'hub', compatibilityDate: '2026-09-03',
        manifest: { mainModule: 'index.mjs', modules: { 'index.mjs': { type: 'esm', contents: script } } },
        env: {
          HUB_DB: { type: 'd1', name: 'HUB_DB' },
          SKILLS_BUCKET: { type: 'r2', name: 'SKILLS_BUCKET' },
          HUB_AUDIENCE: { type: 'text', value: 'public' },
          HUB_ORG: { type: 'text', value: 'public' },
          HUB_INTAKE_ENABLED: { type: 'text', value: 'true' },
          HUB_MONTHLY_CAP_MICROUSD: { type: 'text', value: '10000000' },
        },
        exports: {},
      },
    }],
  });
  t.after(() => runtime.dispose());
  const db = await runtime.getD1Database('HUB_DB');
  await db.batch(MIGRATION.split(';').map(statement => statement.trim()).filter(Boolean).map(statement => db.prepare(statement)));
  const token = Buffer.alloc(32, 7).toString('base64url');
  await db.prepare('INSERT INTO tokens (hash, org, scope, expires, lineage_id, revoked, created) VALUES (?1, ?2, ?3, ?4, ?5, 0, ?6)')
    .bind(await hashToken(token), 'public', 'submit', NOW + 3600, crypto.randomUUID(), NOW).run();
  // Legacy protocol header keeps the request outside the modern MCP envelope; tools/call still runs.
  const call = (payload, headers = {}) => runtime.dispatchFetch('https://hub.example/mcp', {
    method: 'POST',
    headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json', 'mcp-protocol-version': '2025-06-18', ...headers },
    body: typeof payload === 'string' || payload instanceof Uint8Array ? payload : JSON.stringify(payload),
  });
  return { db, call };
}

const submitArguments = (body, extra = {}) => ({
  request_id: crypto.randomUUID(), service_id: 'service-one', kind: 'procedure',
  body, doc_url: 'https://docs.example.com/procedure',
  success_evidence: { action: 'ran it', outcome: 'it worked' }, ...extra,
});
const callTool = (name, args) => ({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name, arguments: args } });
const revision = 'sha256:' + 'a'.repeat(64);

function captureConsole() {
  const messages = [];
  const originals = {};
  for (const method of ['log', 'info', 'warn', 'error', 'debug']) {
    originals[method] = console[method];
    console[method] = (...args) => messages.push(args.map(value => String(value)).join(' '));
  }
  return { messages, restore() { for (const [method, fn] of Object.entries(originals)) console[method] = fn; } };
}

async function assertNothingPersisted(db, samples) {
  for (const table of ['intake', 'events']) {
    const rows = (await db.prepare(`SELECT * FROM ${table}`).all()).results;
    assert.equal(rows.length, 0, `${table} must stay empty after a rejected request`);
    for (const sample of samples) assert.ok(!JSON.stringify(rows).includes(sample), `${table} must never contain a sample`);
  }
}

test('corpus rejects secrets in all seven free-text fields without logging, persistence or echo; allows controls', async t => {
  const fields = {
    body: (sample, revision) => callTool('submit_lesson', submitArguments('# Note ' + sample)),
    doc_url: (sample, revision) => callTool('submit_lesson', submitArguments('# Note', { doc_url: `https://docs.example.com/${encodeURIComponent(sample)}` })),
    submit_action: (sample, revision) => callTool('submit_lesson', submitArguments('# Note', { success_evidence: { action: sample, outcome: 'observed result' } })),
    submit_outcome: (sample, revision) => callTool('submit_lesson', submitArguments('# Note', { success_evidence: { action: 'ran command', outcome: sample } })),
    confirm_action: (sample, revision) => callTool('confirm_lesson', { note_id: 'service/one', revision, success_evidence: { action: sample, outcome: 'observed result' } }),
    confirm_outcome: (sample, revision) => callTool('confirm_lesson', { note_id: 'service/one', revision, success_evidence: { action: 'ran command', outcome: sample } }),
    report_reason: (sample, revision) => callTool('report_failure', { note_id: 'service/one', revision, reason: sample }),
  };
  const capture = captureConsole();
  try {
    for (const [field, requestFor] of Object.entries(fields)) {
      const { db, call } = await setup(t);
      for (const [index, item] of CORPUS.cases.entries()) {
        const sample = assemble(item.parts);
        const uniqueRevision = 'sha256:' + index.toString(16).padStart(64, 'a');
        const response = await call(requestFor(sample, uniqueRevision));
        const text = await response.text();
        if (item.expect === 'reject') {
          assert.equal(response.status, 400, `${item.id}/${field} must reject`);
          assert.equal(JSON.parse(text).error.message, 'SECRET_REJECTED', `${item.id}/${field}`);
          assert.ok(!text.includes(sample), `${item.id}/${field} must not echo the sample`);
        } else {
          assert.equal(response.status, 200, `${item.id}/${field} must be allowed`);
          assert.equal(JSON.parse(text).error, undefined, `${item.id}/${field} must not be rejected`);
        }
      }
      // Accepted controls may persist; rejected cases must not.
      for (const table of ['intake', 'events']) {
        const rows = (await db.prepare(`SELECT * FROM ${table}`).all()).results;
        for (const sample of SAMPLES) assert.ok(!JSON.stringify(rows).includes(sample), `${field} must not persist rejected data`);
      }
    }
  } finally {
    capture.restore();
  }
  assert.equal(capture.messages.length, 0, 'no console output during corpus requests');
});

test('one percent-decoding pass catches an encoded AWS key without storing or echoing it', async t => {
  const { db, call } = await setup(t);
  const sample = ['%41', 'KIA', 'B'.repeat(16)].join('');
  const response = await call(callTool('submit_lesson', submitArguments('A note with ' + sample)));
  const text = await response.text();
  assert.equal(response.status, 400);
  assert.equal(JSON.parse(text).error.message, 'SECRET_REJECTED');
  assert.ok(!text.includes(sample));
  await assertNothingPersisted(db, [sample]);
  const policy = JSON.parse(await readFile(new URL('../../install/secret-patterns.json', import.meta.url), 'utf8'));
  const explicitBundle = (await build({
    entryPoints: [new URL('../src/index.ts', import.meta.url).pathname],
    bundle: true, write: false, format: 'esm', target: 'es2022', platform: 'browser',
    plugins: [{ name: 'explicit-normalization', setup(plugin) {
      plugin.onLoad({ filter: /secret-patterns\.json$/ }, () => ({ contents: JSON.stringify({ ...policy, normalization: ['raw', 'percent-decode-once'] }), loader: 'json' }));
    } }],
  })).outputFiles[0].text;
  const configured = await setup(t, explicitBundle);
  const configuredResponse = await configured.call(callTool('submit_lesson', submitArguments('Encoded ' + sample)));
  assert.equal(configuredResponse.status, 400);
  await assertNothingPersisted(configured.db, [sample]);
  const twiceEncoded = ['%2541', 'KIA', 'B'.repeat(16)].join('');
  const onePass = await call(callTool('submit_lesson', submitArguments('A literal percent encoding ' + twiceEncoded)));
  assert.equal(onePass.status, 200, 'a second percent-decode pass is not performed');
});

test('decoded secrets in RPC id, metadata, unknown keys and document URL never reach D1', async t => {
  const { db, call } = await setup(t);
  const sample = ['%41', 'KIA', 'B'.repeat(16)].join('');
  const base = callTool('submit_lesson', submitArguments('# Clean procedure'));
  const cases = [
    { ...base, id: sample },
    { ...base, params: { ...base.params, _meta: { hint: sample } } },
    { ...base, params: { ...base.params, arguments: { ...base.params.arguments, [sample]: 'untrusted' } } },
    { ...base, params: { ...base.params, arguments: { ...base.params.arguments, doc_url: `https://docs.example.com/${sample}` } } },
  ];
  for (const item of cases) {
    const response = await call(item);
    const body = await response.text();
    assert.equal(response.status, 400);
    assert.equal(JSON.parse(body).error.message, 'SECRET_REJECTED');
    assert.ok(!body.includes(sample));
  }
  await assertNothingPersisted(db, [sample]);
});

test('broken JSON, invalid UTF-8, and oversized bodies carrying a sample are rejected before persistence', async t => {
  const { db, call } = await setup(t);
  const sample = SAMPLES[1];

  const broken = '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"submit_lesson","arguments":{"body":"' + sample + '"}';
  const brokenResponse = await call(broken);
  assert.equal(brokenResponse.status, 400);
  assert.ok(!(await brokenResponse.text()).includes(sample));

  const prefix = new TextEncoder().encode('{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"submit_lesson","arguments":{"body":"' + sample + '"}}}');
  const invalidUtf8 = new Uint8Array([...prefix, 0xff, 0xfe]);
  const utf8Response = await call(invalidUtf8);
  assert.ok(utf8Response.status >= 400);
  assert.ok(!(await utf8Response.text()).includes(sample));

  const oversized = JSON.stringify(callTool('submit_lesson', submitArguments(sample + 'a'.repeat(17000))));
  assert.ok(oversized.length > 16 * 1024);
  const bigResponse = await call(oversized);
  assert.equal(bigResponse.status, 413);
  assert.ok(!(await bigResponse.text()).includes(sample));

  await assertNothingPersisted(db, [sample]);
  assert.equal((await db.prepare('SELECT count(*) AS n FROM tokens').first()).n, 1, 'rejections do not write counters either');
});

test('a safe submission reaches intake without application logging', async t => {
  const { db, call } = await setup(t);
  const capture = captureConsole();
  let response;
  try { response = await call(callTool('submit_lesson', submitArguments('# Clean procedure\n\nA short body.'))); }
  finally { capture.restore(); }
  assert.deepEqual(capture.messages, []);
  const text = await response.text();
  assert.equal(response.status, 200, text.slice(0, 200));
  assert.ok(!text.includes('"error"'), 'the control request is accepted, not a blanket rejection');
  const stored = await db.prepare('SELECT state, kind, service_id FROM intake').first();
  assert.equal(stored.state, 'pending');
  assert.equal(stored.kind, 'procedure');
});
