import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { Miniflare } from 'miniflare';
import { authenticate, createToken, hashToken, manageToken, TOKEN_RE } from '../src/auth.ts';

const MIGRATION = await readFile(new URL('../migrations/0001_intake.sql', import.meta.url), 'utf8');
const NOW = Math.floor(Date.now() / 1000);

async function setup(t) {
  const runtime = new Miniflare({
    telemetry: { enabled: false },
    workers: [{
      config: {
        type: 'worker', name: 'hub', compatibilityDate: '2026-09-03',
        manifest: { mainModule: 'fixture.mjs', modules: { 'fixture.mjs': { type: 'esm', contents: 'export default {async fetch(){return new Response("ok")}}' } } },
        env: { HUB_DB: { type: 'd1', name: 'HUB_DB' } },
        exports: {},
      },
    }],
  });
  t.after(() => runtime.dispose());
  const db = await runtime.getD1Database('HUB_DB');
  // D1 exec() splits on newlines; split statements so the multi-line DDL applies.
  await db.batch(MIGRATION.split(';').map(statement => statement.trim()).filter(Boolean).map(statement => db.prepare(statement)));
  return db;
}

/** Operators are bootstrapped by registering a hash in D1 directly (§A-9); manageToken never mints from nothing. */
async function seedOperator(db, org = 'public', token = createToken()) {
  const hash = await hashToken(token);
  const lineage_id = crypto.randomUUID();
  await db.prepare('INSERT INTO tokens (hash, org, scope, expires, lineage_id, revoked, created) VALUES (?1, ?2, ?3, ?4, ?5, 0, ?6)')
    .bind(hash, org, 'operator', NOW + 3600, lineage_id, NOW).run();
  return { token, hash, org, scope: 'operator', lineage_id, expires: NOW + 3600 };
}

async function issueOperator(db) {
  const issued = await seedOperator(db);
  return { issued, ctx: { db, org: 'public', auth: { hash: issued.hash, org: 'public', scope: 'operator', lineage_id: issued.lineage_id, expires: issued.expires } } };
}

test('issue returns the plaintext once and stores only its SHA-256 hash', async t => {
  const db = await setup(t);
  const { ctx } = await issueOperator(db, t);
  const issued = await manageToken({ action: 'issue', scope: 'operator', expires: NOW + 3600 }, ctx);
  assert.match(issued.token, TOKEN_RE);
  assert.equal(issued.hash, await hashToken(issued.token));
  assert.equal(issued.scope, 'operator');
  assert.match(issued.lineage_id, /^[0-9a-f-]{36}$/);
  const rows = (await db.prepare('SELECT * FROM tokens WHERE hash = ?1').bind(issued.hash).all()).results;
  assert.equal(rows.length, 1);
  assert.equal(rows[0].scope, 'operator');
  assert.equal(rows[0].revoked, 0);
  assert.ok(!JSON.stringify(rows).includes(issued.token), 'the plaintext token never reaches D1');
  await assert.rejects(manageToken({ action: 'issue', scope: 'root', expires: NOW + 60 }, ctx), e => e.code === 'INVALID_PARAMS');
  await assert.rejects(manageToken({ action: 'issue', scope: 'read', expires: NOW - 1 }, ctx), e => e.code === 'INVALID_PARAMS');
  await assert.rejects(manageToken({ action: 'issue', scope: 'read', expires: NOW + 60, extra: 1 }, ctx), e => e.code === 'INVALID_PARAMS');
  await assert.rejects(manageToken({ action: 'issue', scope: 'read', expires: NOW + 60 }, { ...ctx, auth: { ...ctx.auth, scope: 'read' } }), e => e.code === 'FORBIDDEN');
  await assert.rejects(manageToken({ action: 'issue', scope: 'read', expires: NOW + 60 }, { ...ctx, org: 'company' }), e => e.code === 'FORBIDDEN');
});

test('authenticate accepts only a live token of this org and never caches expiry', async t => {
  const db = await setup(t);
  const { issued } = await issueOperator(db, t);
  const env = { HUB_DB: db, HUB_ORG: 'public' };
  const valid = new Request('https://hub.example/mcp', { headers: { authorization: `Bearer ${issued.token}` } });
  const auth = await authenticate(valid, env);
  assert.deepEqual(auth, { hash: issued.hash, org: 'public', scope: 'operator', lineage_id: issued.lineage_id, expires: issued.expires });
  assert.ok(await authenticate(new Request('https://hub.example/mcp', { headers: { authorization: `bearer ${issued.token}` } }), env), 'the auth scheme is case-insensitive');
  const denied = [
    new Request('https://hub.example/mcp'),
    new Request('https://hub.example/mcp', { headers: { authorization: issued.token } }),
    new Request('https://hub.example/mcp', { headers: { authorization: `Bearer  ${issued.token}` } }),
    new Request('https://hub.example/mcp', { headers: { authorization: `Bearer ${'a'.repeat(42)}` } }),
    new Request('https://hub.example/mcp', { headers: { authorization: `Bearer ${'a'.repeat(43)}` } }),
  ];
  for (const request of denied) await assert.rejects(authenticate(request, env), e => e.code === 'UNAUTHORIZED');
  await db.prepare('UPDATE tokens SET expires = ?1 WHERE hash = ?2').bind(NOW - 1, issued.hash).run();
  await assert.rejects(authenticate(valid, env), e => e.code === 'UNAUTHORIZED', 'expiry is re-checked, not cached');
  await db.prepare('UPDATE tokens SET expires = ?1, org = ?2 WHERE hash = ?3').bind(NOW + 3600, 'company', issued.hash).run();
  await assert.rejects(authenticate(valid, env), e => e.code === 'UNAUTHORIZED', 'a token from another org never authenticates');
});

test('a short existing reader bearer works after its hash is registered without rotating it', async t => {
  const db = await setup(t);
  const legacy = 'reader-old';
  const hash = await hashToken(legacy);
  await db.prepare('INSERT INTO tokens(hash,org,scope,expires,lineage_id,created) VALUES(?,?,?,?,?,?)')
    .bind(hash, 'public', 'read', NOW + 3600, crypto.randomUUID(), NOW).run();
  const request = new Request('https://hub.example/mcp', { headers: { authorization: `Bearer ${legacy}` } });
  assert.equal((await authenticate(request, { HUB_DB: db, HUB_ORG: 'public' })).scope, 'read');
  await db.prepare('UPDATE tokens SET revoked=1 WHERE hash=?').bind(hash).run();
  await assert.rejects(authenticate(request, { HUB_DB: db, HUB_ORG: 'public' }), e => e.code === 'UNAUTHORIZED');
});

test('reissue is atomic per lineage and revoke is idempotent', async t => {
  const db = await setup(t);
  const { ctx } = await issueOperator(db, t);
  const first = await manageToken({ action: 'issue', scope: 'submit', expires: NOW + 3600 }, ctx);
  const second = await manageToken({ action: 'reissue', token_hash: first.hash, expires: NOW + 7200 }, ctx);
  assert.equal(second.lineage_id, first.lineage_id, 'reissue keeps the lineage');
  assert.equal(second.org, first.org);
  assert.equal(second.scope, first.scope);
  assert.notEqual(second.hash, first.hash);
  await assert.rejects(authenticate(new Request('https://hub.example/mcp', { headers: { authorization: `Bearer ${first.token}` } }), { HUB_DB: db, HUB_ORG: 'public' }), e => e.code === 'UNAUTHORIZED');
  assert.ok(await authenticate(new Request('https://hub.example/mcp', { headers: { authorization: `Bearer ${second.token}` } }), { HUB_DB: db, HUB_ORG: 'public' }));
  await assert.rejects(manageToken({ action: 'reissue', token_hash: first.hash, expires: NOW + 7200 }, ctx), e => e.code === 'CONFLICT', 'a revoked token cannot be reissued again');

  const fresh = await manageToken({ action: 'issue', scope: 'submit', expires: NOW + 3600 }, ctx);
  const race = await Promise.allSettled([
    manageToken({ action: 'reissue', token_hash: fresh.hash, expires: NOW + 7200 }, ctx),
    manageToken({ action: 'reissue', token_hash: fresh.hash, expires: NOW + 7200 }, ctx),
  ]);
  assert.equal(race.filter(outcome => outcome.status === 'fulfilled').length, 1, 'only one concurrent reissue wins');
  assert.equal(race.filter(outcome => outcome.status === 'rejected' && outcome.reason.code === 'CONFLICT').length, 1);
  const active = await db.prepare('SELECT count(*) AS n FROM tokens WHERE lineage_id = ?1 AND revoked = 0').bind(fresh.lineage_id).first();
  assert.equal(active.n, 1, 'never two live tokens for one lineage');

  const revoked = await manageToken({ action: 'revoke', token_hash: second.hash }, ctx);
  assert.equal(revoked.revoked, true);
  assert.equal(revoked.duplicate, false);
  const again = await manageToken({ action: 'revoke', token_hash: second.hash }, ctx);
  assert.equal(again.duplicate, true, 'repeated revoke is a no-op');
  await assert.rejects(manageToken({ action: 'revoke', token_hash: 'f'.repeat(64) }, ctx), e => e.code === 'NOT_FOUND');
});

test('operator cannot reissue or revoke a token belonging to another org in the same D1', async t => {
  const db = await setup(t);
  const { ctx } = await issueOperator(db, t);
  const company = await seedOperator(db, 'company');
  const foreign = await manageToken({ action: 'issue', scope: 'submit', expires: NOW + 3600 }, { db, org: 'company', auth: { hash: company.hash, org: 'company', scope: 'operator', lineage_id: company.lineage_id, expires: company.expires } });
  await assert.rejects(manageToken({ action: 'reissue', token_hash: foreign.hash, expires: NOW + 7200 }, ctx), e => e.code === 'CONFLICT');
  await assert.rejects(manageToken({ action: 'revoke', token_hash: foreign.hash }, ctx), e => e.code === 'NOT_FOUND');
  const row = await db.prepare('SELECT revoked FROM tokens WHERE hash=?').bind(foreign.hash).first();
  assert.equal(row.revoked, 0);
});

test('token management rechecks the calling operator inside the write, not from the stale auth context', async t => {
  const db = await setup(t);
  const { ctx } = await issueOperator(db);
  const target = await manageToken({ action: 'issue', scope: 'submit', expires: NOW + 3600 }, ctx);
  const count = async () => (await db.prepare('SELECT count(*) AS n FROM tokens').first()).n;
  // Authentication happened earlier; the caller is revoked while the body is still streaming.
  await db.prepare('UPDATE tokens SET revoked = 1 WHERE hash = ?1').bind(ctx.auth.hash).run();
  const before = await count();
  await assert.rejects(manageToken({ action: 'issue', scope: 'operator', expires: NOW + 3600 }, ctx), e => e.code === 'UNAUTHORIZED');
  await assert.rejects(manageToken({ action: 'reissue', token_hash: target.hash, expires: NOW + 7200 }, ctx), e => e.code === 'UNAUTHORIZED');
  await assert.rejects(manageToken({ action: 'revoke', token_hash: target.hash }, ctx), e => e.code === 'UNAUTHORIZED');
  assert.equal(await count(), before, 'a revoked caller mints nothing');
  assert.equal((await db.prepare('SELECT revoked FROM tokens WHERE hash = ?1').bind(target.hash).first()).revoked, 0, 'a revoked caller revokes nothing');

  const { ctx: expiring } = await issueOperator(db);
  await db.prepare('UPDATE tokens SET expires = ?1 WHERE hash = ?2').bind(NOW - 1, expiring.auth.hash).run();
  await assert.rejects(manageToken({ action: 'issue', scope: 'read', expires: NOW + 3600 }, expiring), e => e.code === 'UNAUTHORIZED');

  const { ctx: demoted } = await issueOperator(db);
  await db.prepare("UPDATE tokens SET scope = 'submit' WHERE hash = ?1").bind(demoted.auth.hash).run();
  await assert.rejects(manageToken({ action: 'issue', scope: 'read', expires: NOW + 3600 }, demoted), e => e.code === 'UNAUTHORIZED');
  assert.equal(await count(), before + 2);
});

test('an existing D1-registered legacy bearer authenticates; new tokens keep the 43-char format', async t => {
  const db = await setup(t);
  const env = { HUB_DB: db, HUB_ORG: 'public' };
  const legacy = await seedOperator(db, 'public', 'direct-worker-fixture-token');
  const bearer = token => new Request('https://hub.example/mcp', { headers: { authorization: `Bearer ${token}` } });
  const auth = await authenticate(bearer(legacy.token), env);
  assert.equal(auth.hash, legacy.hash);
  await assert.rejects(authenticate(bearer('unregistered-legacy-token'), env), e => e.code === 'UNAUTHORIZED');
  const tooLong = await seedOperator(db, 'public', 'x'.repeat(1025));
  await assert.rejects(authenticate(bearer(tooLong.token), env), e => e.code === 'UNAUTHORIZED', 'bearer length is bounded before hashing');
  const spaced = await seedOperator(db, 'public', 'legacy token with spaces');
  await assert.rejects(authenticate(bearer(spaced.token), env), e => e.code === 'UNAUTHORIZED');
  const { ctx } = await issueOperator(db);
  assert.match((await manageToken({ action: 'issue', scope: 'read', expires: NOW + 3600 }, ctx)).token, TOKEN_RE);
});
