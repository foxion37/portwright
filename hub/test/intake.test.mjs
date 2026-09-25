import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { Miniflare } from 'miniflare';
import { createToken, hashToken, manageToken } from '../src/auth.ts';
import { confirmLesson, listEvents, listIntake, reportFailure, submitLesson, transitionIntake } from '../src/intake.ts';

const MIGRATION = await readFile(new URL('../migrations/0001_intake.sql', import.meta.url), 'utf8');
const NOW = Math.floor(Date.now() / 1000);
const DAY = Math.floor(NOW / 86400);
const digest = seed => 'sha256:' + [String(seed)].map(ch => ch.charCodeAt(0).toString(16)).join('').padEnd(64, '0');

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
  const operator = await seedOperator(db);
  return { db, operator };
}

/** Operators are bootstrapped by registering a hash in D1 directly (§A-9). */
async function seedOperator(db, org = 'public') {
  const hash = await hashToken(createToken());
  const lineage_id = crypto.randomUUID();
  await db.prepare('INSERT INTO tokens (hash, org, scope, expires, lineage_id, revoked, created) VALUES (?1, ?2, ?3, ?4, ?5, 0, ?6)')
    .bind(hash, org, 'operator', NOW + 3600, lineage_id, NOW).run();
  return { db, org, auth: { hash, org, scope: 'operator', lineage_id, expires: NOW + 3600 } };
}

/** Runs `interfere` right before the first statement matching `pattern` executes: a deterministic race. */
function raceBefore(db, pattern, interfere) {
  let armed = true;
  return new Proxy(db, {
    get(target, prop) {
      if (prop !== 'prepare') { const value = target[prop]; return typeof value === 'function' ? value.bind(target) : value; }
      return sql => {
        const statement = target.prepare(sql);
        if (!armed || !pattern.test(sql)) return statement;
        return { bind: (...args) => {
          const bound = statement.bind(...args);
          const fire = method => async () => { if (armed) { armed = false; await interfere(); } return bound[method](); };
          return { run: fire('run'), all: fire('all'), first: fire('first') };
        } };
      };
    },
  });
}

async function submitContext(db, operator, scope = 'submit') {
  const issued = await manageToken({ action: 'issue', scope, expires: NOW + 3600 }, operator);
  return { issued, ctx: { db, org: 'public', auth: { hash: issued.hash, org: 'public', scope, lineage_id: issued.lineage_id, expires: issued.expires } } };
}

let sequence = 0;
const submitInput = (overrides = {}) => ({
  request_id: crypto.randomUUID(), service_id: 'service-' + (++sequence), kind: 'procedure',
  body: '# Procedure\n\nSteps.', doc_url: 'https://docs.example.com/' + sequence,
  success_evidence: { action: 'ran the procedure', outcome: 'step 3 returned 200' },
  ...overrides,
});
const evidence = (overrides = {}) => ({ action: 'ran it', outcome: 'it worked', ...overrides });

test('submit is idempotent per lineage/request_id and binds the request digest', async t => {
  const { db, operator } = await setup(t);
  const { issued, ctx } = await submitContext(db, operator);
  const input = submitInput();
  const created = await submitLesson(input, ctx);
  assert.equal(created.state, 'pending');
  assert.equal(created.duplicate, false);
  const replay = await submitLesson(input, ctx);
  assert.equal(replay.intake_id, created.intake_id);
  assert.equal(replay.duplicate, true);
  await assert.rejects(submitLesson({ ...input, body: '# Other' }, ctx), e => e.code === 'IDEMPOTENCY_CONFLICT', 'same request_id with other content is a conflict');
  const row = await db.prepare('SELECT * FROM intake WHERE id = ?1').bind(created.intake_id).first();
  assert.equal(row.token_hash, issued.hash);
  assert.equal(row.lineage_id, issued.lineage_id);
  assert.equal(row.day, DAY);
  assert.equal(row.state, 'pending');
  assert.equal(row.body, input.body, 'accepted text is stored for the workflow');

  await assert.rejects(submitLesson(submitInput({ note_id: 'service/existing' }), ctx), e => e.code === 'INVALID_PARAMS', 'note_id requires expected_revision');
  const shared = submitInput({ note_id: 'service/existing', expected_revision: digest('a') });
  const first = await submitLesson(shared, ctx);
  const other = await submitContext(db, operator);
  const second = await submitLesson(shared, other.ctx);
  assert.notEqual(first.intake_id, second.intake_id, 'the same request_id from another lineage is a new intake');
});

test('submissions and confirmations share the 20/day token budget, atomically', async t => {
  const { db, operator } = await setup(t);
  const { issued, ctx } = await submitContext(db, operator);
  for (let i = 0; i < 19; i++) assert.equal((await submitLesson(submitInput(), ctx)).duplicate, false);
  const first = await confirmLesson({ note_id: 'service/example', revision: digest('a'), success_evidence: evidence() }, ctx);
  assert.equal(first.validation, 'pending');
  assert.equal(first.duplicate, false);
  const used = await db.prepare(
    "SELECT (SELECT count(*) FROM intake WHERE token_hash = ?1 AND day = ?2) " +
    "     + (SELECT count(*) FROM events WHERE token_hash = ?1 AND kind = 'confirm' AND day = ?2) AS used",
  ).bind(issued.hash, DAY).first();
  assert.equal(used.used, 20);
  await assert.rejects(submitLesson(submitInput(), ctx), e => e.code === 'DAILY_LIMIT');
  await assert.rejects(confirmLesson({ note_id: 'service/other', revision: digest('b'), success_evidence: evidence() }, ctx), e => e.code === 'DAILY_LIMIT');

  const { ctx: fresh } = await submitContext(db, operator);
  const race = await Promise.allSettled([submitLesson(submitInput(), fresh), submitLesson(submitInput(), fresh)]);
  assert.equal(race.filter(outcome => outcome.status === 'fulfilled').length, 2, 'a fresh token has its own budget');
});

test('confirm is one unique vote per lineage and validate_confirm is a CAS', async t => {
  const { db, operator } = await setup(t);
  const { ctx } = await submitContext(db, operator);
  const revision = digest('c');
  const vote = await confirmLesson({ note_id: 'service/one', revision, success_evidence: evidence() }, ctx);
  assert.equal(vote.duplicate, false);
  const replay = await confirmLesson({ note_id: 'service/one', revision, success_evidence: evidence() }, ctx);
  assert.equal(replay.duplicate, true);
  assert.equal(replay.validation, 'pending');
  const event = await db.prepare("SELECT id, payload FROM events WHERE kind = 'confirm' AND note_id = ?1").bind('service/one').first();
  const payloadDigest = JSON.parse(event.payload).payload_digest;
  const gate = digest('d');
  await assert.rejects(transitionIntake({ action: 'validate_confirm', event_id: event.id, expected_payload_digest: 'e'.repeat(64), gate_digest: gate, validation: 'passed' }, operator), e => e.code === 'CONFLICT');
  const passed = await transitionIntake({ action: 'validate_confirm', event_id: event.id, expected_payload_digest: payloadDigest, gate_digest: gate, validation: 'passed' }, operator);
  assert.equal(passed.validation, 'passed');
  const dupe = await transitionIntake({ action: 'validate_confirm', event_id: event.id, expected_payload_digest: payloadDigest, gate_digest: gate, validation: 'passed' }, operator);
  assert.equal(dupe.duplicate, true);
  const replaced = await confirmLesson({ note_id: 'service/one', revision, success_evidence: evidence({ action: 'different action' }) }, ctx);
  assert.equal(replaced.duplicate, true);
  assert.equal(replaced.validation, 'passed', 'replacing evidence on a passed vote is a no-op');
  assert.equal(JSON.parse((await db.prepare('SELECT payload FROM events WHERE id = ?1').bind(event.id).first()).payload).payload_digest, payloadDigest);
});

test('same-token held evidence may be replaced only on a later UTC day', async t => {
  const { db, operator } = await setup(t);
  const { issued, ctx } = await submitContext(db, operator);
  const vote = action => ({ note_id: 'service/held', revision: digest('a'), success_evidence: evidence({ action }) });
  await confirmLesson(vote('original'), ctx);
  const event = await db.prepare("SELECT id, payload FROM events WHERE kind = 'confirm'").first();
  await transitionIntake({ action: 'validate_confirm', event_id: event.id, expected_payload_digest: JSON.parse(event.payload).payload_digest, gate_digest: digest('g'), validation: 'held' }, operator);
  await assert.rejects(confirmLesson(vote('same day'), ctx), e => e.code === 'DAILY_LIMIT');
  assert.equal(JSON.parse((await db.prepare('SELECT payload FROM events WHERE id = ?').bind(event.id).first()).payload).validation, 'held');
  await db.prepare('UPDATE events SET day = ? WHERE id = ?').bind(DAY - 1, event.id).run();
  const replaced = await confirmLesson(vote('next day'), ctx);
  assert.equal(replaced.validation, 'pending');
  assert.equal((await db.prepare('SELECT day FROM events WHERE id = ?').bind(event.id).first()).day, DAY);
  await assert.rejects(confirmLesson(vote('another replacement'), ctx), e => e.code === 'DAILY_LIMIT');
  const used = await db.prepare("SELECT count(*) AS n FROM events WHERE token_hash = ? AND kind = 'confirm' AND day = ?").bind(issued.hash, DAY).first();
  assert.equal(used.n, 1);
});

test('a reissued token re-confirms a passed vote as one pending vote under its live hash', async t => {
  const { db, operator } = await setup(t);
  const { issued, ctx } = await submitContext(db, operator);
  const revision = digest('e');
  const vote = { note_id: 'service/validated', revision, success_evidence: evidence() };
  await confirmLesson(vote, ctx);
  const event = await db.prepare("SELECT id,payload FROM events WHERE kind='confirm' AND note_id=?").bind(vote.note_id).first();
  await transitionIntake({ action: 'validate_confirm', event_id: event.id, expected_payload_digest: JSON.parse(event.payload).payload_digest, gate_digest: digest('d'), validation: 'passed' }, operator);
  const reissued = await manageToken({ action: 'reissue', token_hash: issued.hash, expires: NOW + 7200 }, operator);
  const next = { db, org: 'public', auth: { ...ctx.auth, hash: reissued.hash, expires: reissued.expires } };
  const result = await confirmLesson({ ...vote, success_evidence: evidence({ action: 're-attested' }) }, next);
  assert.equal(result.validation, 'pending');
  const eligible = await listEvents({ note_id: vote.note_id, revision }, operator);
  assert.equal(eligible.items.length, 1);
  assert.equal(eligible.items[0].eligible, true);
  assert.equal(eligible.items[0].token_hash, reissued.hash);
});

test('a revoked token gets no vote and reissue re-confirms the same lineage vote row', async t => {
  const { db, operator } = await setup(t);
  const { issued, ctx } = await submitContext(db, operator);
  const revision = digest('f');
  await confirmLesson({ note_id: 'service/reissue', revision, success_evidence: evidence() }, ctx);
  const reissued = await manageToken({ action: 'reissue', token_hash: issued.hash, expires: NOW + 7200 }, operator);
  await assert.rejects(confirmLesson({ note_id: 'service/reissue', revision, success_evidence: evidence({ action: 'again' }) }, ctx), e => e.code === 'UNAUTHORIZED', 'a revoked token cannot re-confirm');
  const next = { db, org: 'public', auth: { hash: reissued.hash, org: 'public', scope: 'submit', lineage_id: reissued.lineage_id, expires: reissued.expires } };
  const moved = await confirmLesson({ note_id: 'service/reissue', revision, success_evidence: evidence({ action: 'from new token' }) }, next);
  assert.equal(moved.duplicate, true);
  assert.equal(moved.validation, 'pending', 'a fresh pending vote is restored for the new evidence');
  const rows = await db.prepare("SELECT count(*) AS n FROM events WHERE kind = 'confirm' AND note_id = ?1 AND revision = ?2").bind('service/reissue', revision).first();
  assert.equal(rows.n, 1, 'the reissued lineage still has exactly one independent vote');
  const moved2 = await confirmLesson({ note_id: 'service/reissue', revision, success_evidence: evidence({ action: 'from new token' }) }, next);
  assert.equal(moved2.duplicate, true);
  assert.equal(JSON.parse((await db.prepare("SELECT payload FROM events WHERE kind = 'confirm' AND note_id = ?1").bind('service/reissue').first()).payload).validation, 'pending');
});

test('reports are limited to 10 distinct revisions per token per day, one vote per lineage', async t => {
  const { db, operator } = await setup(t);
  const { issued, ctx } = await submitContext(db, operator);
  for (let i = 0; i < 10; i++) {
    const result = await reportFailure({ note_id: 'service/rep', revision: digest(String(i % 16)), reason: 'step ' + i + ' failed' }, ctx);
    assert.equal(result.duplicate, false);
  }
  assert.equal((await db.prepare("SELECT count(*) AS n FROM events WHERE kind = 'report' AND token_hash = ?1").bind(issued.hash).first()).n, 10);
  await assert.rejects(reportFailure({ note_id: 'service/rep', revision: digest('a'), reason: 'one more' }, ctx), e => e.code === 'DAILY_LIMIT');
  const replay = await reportFailure({ note_id: 'service/rep', revision: digest('0'), reason: 'step 0 failed' }, ctx);
  assert.equal(replay.duplicate, true, 'unchanged reason remains idempotent');
  await assert.rejects(reportFailure({ note_id: 'service/rep', revision: digest('0'), reason: 'corrected reason' }, ctx), e => e.code === 'DAILY_LIMIT');
  const rows = await db.prepare("SELECT count(*) AS n FROM events WHERE kind = 'report' AND note_id = 'service/rep' AND revision = ?1").bind(digest('0')).first();
  assert.equal(rows.n, 1);
  await assert.rejects(reportFailure({ note_id: 'service/rep', revision: digest('0'), reason: 'x' }, { ...ctx, auth: { ...ctx.auth, hash: 'f'.repeat(64) } }), e => e.code === 'UNAUTHORIZED');
});

test('same-token report reason changes require a new UTC day', async t => {
  const { db, operator } = await setup(t);
  const { ctx } = await submitContext(db, operator);
  const report = reason => ({ note_id: 'service/rep', revision: digest('a'), reason });
  await reportFailure(report('first reason'), ctx);
  await assert.rejects(reportFailure(report('same day change'), ctx), e => e.code === 'DAILY_LIMIT');
  const event = await db.prepare("SELECT id, payload FROM events WHERE kind = 'report'").first();
  assert.equal(JSON.parse(event.payload).reason, 'first reason');
  await db.prepare('UPDATE events SET day = ? WHERE id = ?').bind(DAY - 1, event.id).run();
  assert.equal((await reportFailure(report('next day change'), ctx)).duplicate, true);
  const updated = await db.prepare('SELECT day, payload FROM events WHERE id = ?').bind(event.id).first();
  assert.equal(updated.day, DAY);
  assert.equal(JSON.parse(updated.payload).reason, 'next day change');
});

test('operator rejects identifier-bearing confirmation and redacts report reason with digest CAS', async t => {
  const { db, operator } = await setup(t);
  const { ctx } = await submitContext(db, operator);
  const revision = digest('i');
  const marker = ['synthetic-person', '@example.org'].join('');
  await confirmLesson({ note_id: 'service/privacy', revision, success_evidence: evidence({ action: marker }) }, ctx);
  await reportFailure({ note_id: 'service/privacy', revision, reason: marker }, ctx);
  const listed = (await listEvents({ note_id: 'service/privacy', revision }, operator)).items;
  const confirm = listed.find(item => item.kind === 'confirm');
  const report = listed.find(item => item.kind === 'report');
  const reject = { action: 'validate_confirm', event_id: confirm.id, expected_payload_digest: confirm.payload.payload_digest, gate_digest: digest('g'), validation: 'rejected' };
  const redact = { action: 'redact_report', event_id: report.id, expected_payload_digest: report.payload.payload_digest };
  await assert.rejects(transitionIntake({ ...reject, expected_payload_digest: 'f'.repeat(64) }, operator), e => e.code === 'CONFLICT');
  await assert.rejects(transitionIntake({ ...redact, expected_payload_digest: 'f'.repeat(64) }, operator), e => e.code === 'CONFLICT');
  await assert.rejects(transitionIntake(redact, ctx), e => e.code === 'FORBIDDEN');
  assert.equal((await transitionIntake(reject, operator)).validation, 'rejected');
  assert.equal((await transitionIntake(redact, operator)).duplicate, false);
  assert.equal((await transitionIntake(reject, operator)).duplicate, true);
  assert.equal((await transitionIntake(redact, operator)).duplicate, true);
  const after = (await listEvents({ note_id: 'service/privacy', revision }, operator)).items;
  assert.equal(after.find(item => item.kind === 'confirm').payload.validation, 'rejected');
  assert.equal('success_evidence' in after.find(item => item.kind === 'confirm').payload, false);
  assert.equal('reason' in after.find(item => item.kind === 'report').payload, false);
  assert.equal(JSON.stringify(after).includes(marker), false);
});

test('listIntake and listEvents page by (created,id) and join current token eligibility', async t => {
  const { db, operator } = await setup(t);
  const { issued, ctx } = await submitContext(db, operator);
  const ids = [];
  for (let i = 0; i < 7; i++) ids.push((await submitLesson(submitInput(), ctx)).intake_id);
  await transitionIntake({ action: 'hold', intake_id: ids[0], reason_code: 'policy_review' }, operator);
  const held = await listIntake({ state: 'held' }, operator);
  assert.equal(held.items.length, 1);
  assert.equal(held.items[0].id, ids[0]);
  assert.equal(held.items[0].state, 'held');
  assert.equal(held.items[0].eligible, true);
  assert.deepEqual(held.items[0].success_evidence, { action: 'ran the procedure', outcome: 'step 3 returned 200' });
  const page = await listIntake({ state: 'pending', limit: 3 }, operator);
  assert.equal(page.items.length, 3);
  assert.ok(page.next_cursor);
  const next = await listIntake({ state: 'pending', limit: 3, after: page.next_cursor }, operator);
  assert.equal(next.items.length, 3);
  assert.ok(!page.items.some(item => next.items.some(other => other.id === item.id)), 'cursor never repeats a row');
  await assert.rejects(listIntake({ state: 'rejected' }, operator), e => e.code === 'INVALID_PARAMS');
  await assert.rejects(listIntake({ limit: 21 }, operator), e => e.code === 'INVALID_PARAMS');

  const revision = digest('g');
  await confirmLesson({ note_id: 'service/listed', revision, success_evidence: evidence() }, ctx);
  await reportFailure({ note_id: 'service/listed', revision, reason: 'broken' }, ctx);
  const events = await listEvents({ note_id: 'service/listed', revision }, operator);
  assert.equal(events.items.length, 2);
  assert.deepEqual([...new Set(events.items.map(item => item.kind))].sort(), ['confirm', 'report']);
  assert.ok(events.items.every(item => item.eligible === true));
  assert.equal(typeof events.items[0].payload.payload_digest, 'string');
  await manageToken({ action: 'revoke', token_hash: issued.hash }, operator);
  const after = await listEvents({ note_id: 'service/listed', revision }, operator);
  assert.ok(after.items.every(item => item.eligible === false), 'events of a revoked token are marked ineligible');
  const intakeAfter = await listIntake({ state: 'pending', limit: 20 }, operator);
  assert.ok(intakeAfter.items.every(item => item.eligible === false));
  await assert.rejects(listEvents({ revision }, operator), e => e.code === 'INVALID_PARAMS', 'revision without note_id is rejected');
  await assert.rejects(listEvents({ note_id: 'service/listed' }, operator), e => e.code === 'INVALID_PARAMS');
});

test('admin queue and event listing stays within the authenticated org', async t => {
  const { db, operator } = await setup(t);
  const foreignOperator = await seedOperator(db, 'company');
  const issued = await manageToken({ action: 'issue', scope: 'submit', expires: NOW + 3600 }, foreignOperator);
  const ctx = { db, org: 'company', auth: { hash: issued.hash, org: 'company', scope: 'submit', lineage_id: issued.lineage_id, expires: issued.expires } };
  const foreign = await submitLesson(submitInput(), ctx);
  await reportFailure({ note_id: 'service/foreign', revision: digest('z'), reason: 'foreign note' }, ctx);
  assert.deepEqual((await listIntake({ state: 'pending' }, operator)).items, []);
  assert.deepEqual((await listEvents({ note_id: 'service/foreign', revision: digest('z') }, operator)).items, []);
  await assert.rejects(transitionIntake({ action: 'reject', intake_id: foreign.intake_id, reason_code: 'identifier' }, operator), e => e.code === 'NOT_FOUND' || e.code === 'FORBIDDEN');
  assert.equal((await db.prepare('SELECT state FROM intake WHERE id=?').bind(foreign.intake_id).first()).state, 'pending');
});

test('revoking an operator prevents later review receipts from its stale context', async t => {
  const { db, operator } = await setup(t);
  await manageToken({ action: 'revoke', token_hash: operator.auth.hash }, operator);
  await assert.rejects(transitionIntake({ action: 'review', note_id: 'service/example', revision: digest('a'), decision: 'promote', gate_digest: digest('b'), events_digest: digest('c'), request_id: crypto.randomUUID() }, operator), e => e.code === 'UNAUTHORIZED');
  assert.equal((await db.prepare("SELECT count(*) AS n FROM events WHERE kind='ack'").first()).n, 0);
});

test('ack cannot rewrite a committed git receipt using another commit', async t => {
  const { db, operator } = await setup(t);
  const { ctx } = await submitContext(db, operator);
  const { intake_id } = await submitLesson(submitInput(), ctx);
  const revision = digest('a');
  await transitionIntake({ action: 'committed', intake_id, note_id: 'service/receipt', revision, gate_digest: digest('b'), commit: 'a'.repeat(40) }, operator);
  const ack = { action: 'ack', intake_id, revision, commit: 'b'.repeat(40), release_commit: 'c'.repeat(40) };
  await assert.rejects(transitionIntake(ack, operator), e => e.code === 'CONFLICT');
  const still = await db.prepare('SELECT state, commit_sha, body FROM intake WHERE id = ?').bind(intake_id).first();
  assert.equal(still.state, 'committed');
  assert.equal(still.commit_sha, 'a'.repeat(40));
  assert.ok(still.body);
  assert.equal((await transitionIntake({ ...ack, commit: 'a'.repeat(40) }, operator)).state, 'published');
  await assert.rejects(transitionIntake(ack, operator), e => e.code === 'CONFLICT');
  assert.equal((await db.prepare('SELECT commit_sha FROM intake WHERE id = ?').bind(intake_id).first()).commit_sha, 'a'.repeat(40));
});

test('transitions keep the receipt and erase text at published, rejected, and acked', async t => {
  const { db, operator } = await setup(t);
  const { ctx } = await submitContext(db, operator);
  const revision = digest('h');
  const pending = await submitLesson(submitInput(), ctx);
  const committed = await transitionIntake({ action: 'committed', intake_id: pending.intake_id, note_id: 'service/new', revision, gate_digest: digest('i'), commit: 'a'.repeat(40) }, operator);
  assert.equal(committed.state, 'committed');
  assert.equal(committed.duplicate, false);
  assert.ok((await db.prepare('SELECT body FROM intake WHERE id = ?1').bind(pending.intake_id).first()).body, 'committed intake keeps its text for the workflow');
  const queued = await listIntake({ state: 'committed' }, operator);
  assert.equal(queued.items[0].id, pending.intake_id);
  assert.equal(queued.items[0].note_id, 'service/new');
  assert.equal(queued.items[0].revision, revision);
  assert.equal(queued.items[0].gate_digest, digest('i'));
  assert.equal(queued.items[0].commit_sha, 'a'.repeat(40));
  const duplicateCommit = await transitionIntake({ action: 'committed', intake_id: pending.intake_id, note_id: 'service/new', revision, gate_digest: digest('i'), commit: 'a'.repeat(40) }, operator);
  assert.equal(duplicateCommit.duplicate, true);
  const published = await transitionIntake({ action: 'ack', intake_id: pending.intake_id, revision, commit: 'a'.repeat(40), release_commit: 'b'.repeat(40) }, operator);
  assert.equal(published.state, 'published');
  const row = await db.prepare('SELECT * FROM intake WHERE id = ?1').bind(pending.intake_id).first();
  assert.equal(row.body, null);
  assert.equal(row.doc_url, null);
  assert.equal(row.success_evidence, null);
  assert.equal(row.published_commit, 'b'.repeat(40));
  assert.equal(row.revision, revision);

  const rejected = await submitLesson(submitInput(), ctx);
  await transitionIntake({ action: 'reject', intake_id: rejected.intake_id, reason_code: 'identifier' }, operator);
  const gone = await db.prepare('SELECT * FROM intake WHERE id = ?1').bind(rejected.intake_id).first();
  assert.equal(gone.state, 'rejected');
  assert.equal(gone.body, null);
  assert.equal(gone.doc_url, null);
  assert.equal(gone.success_evidence, null);

  const terminal = await submitLesson(submitInput(), ctx);
  await transitionIntake({ action: 'committed', intake_id: terminal.intake_id, note_id: 'service/terminal', revision, gate_digest: digest('j'), commit: 'c'.repeat(40) }, operator);
  await assert.rejects(transitionIntake({ action: 'reject', intake_id: terminal.intake_id, reason_code: 'identifier' }, operator), e => e.code === 'INVALID_PARAMS', 'a committed intake only ends on a terminal reason');
  await transitionIntake({ action: 'reject', intake_id: terminal.intake_id, reason_code: 'superseded' }, operator);
  assert.equal((await db.prepare('SELECT state FROM intake WHERE id = ?1').bind(terminal.intake_id).first()).state, 'rejected');
  await assert.rejects(transitionIntake({ action: 'hold', intake_id: terminal.intake_id, reason_code: 'policy_review' }, operator), e => e.code === 'CONFLICT');
  await assert.rejects(transitionIntake({ action: 'committed', intake_id: crypto.randomUUID(), note_id: 'service/new', revision, gate_digest: digest('i'), commit: 'a'.repeat(40) }, operator), e => e.code === 'NOT_FOUND');

  const requestId = crypto.randomUUID();
  const review = { action: 'review', note_id: 'service/new', revision, decision: 'promote', gate_digest: digest('k'), events_digest: digest('b'), request_id: requestId };
  const firstReview = await transitionIntake(review, operator);
  assert.equal(firstReview.duplicate, false);
  const reviewRow = await db.prepare("SELECT action, revision, payload FROM events WHERE kind = 'ack' AND operation_id = ?1").bind(requestId).first();
  assert.equal(reviewRow.action, 'review_promote');
  assert.equal(reviewRow.revision, revision);
  const reviewPayload = JSON.parse(reviewRow.payload);
  assert.equal(reviewPayload.revision, revision, 'H3 matches reviews on payload.revision');
  assert.equal(reviewPayload.events_digest, digest('b'), 'the sha256:-prefixed events digest is stored as sent');
  assert.equal(reviewPayload.gate_digest, digest('k'));
  const replay = await transitionIntake(review, operator);
  assert.equal(replay.duplicate, true);
  await assert.rejects(transitionIntake({ ...review, events_digest: digest('e') }, operator), e => e.code === 'IDEMPOTENCY_CONFLICT');
  await assert.rejects(transitionIntake({ ...review, request_id: crypto.randomUUID(), events_digest: 'b'.repeat(64) }, operator), e => e.code === 'INVALID_PARAMS', 'events_digest uses the canonical sha256: form');
  await assert.rejects(transitionIntake(review, ctx), e => e.code === 'FORBIDDEN', 'submit scope cannot review');
});

test('a transition that loses a concurrent UPDATE never returns a fabricated receipt', async t => {
  const { db, operator } = await setup(t);
  const { ctx } = await submitContext(db, operator);
  const revision = digest('r');
  const commit = (intake_id, overrides = {}) => ({ action: 'committed', intake_id, note_id: 'service/race', revision, gate_digest: digest('s'), commit: 'a'.repeat(40), ...overrides });
  const racing = (interfere, pattern = /^UPDATE intake/) => ({ ...operator, db: raceBefore(db, pattern, interfere) });

  const conflicting = await submitLesson(submitInput(), ctx);
  await assert.rejects(
    transitionIntake(commit(conflicting.intake_id), racing(() => transitionIntake(commit(conflicting.intake_id, { commit: 'b'.repeat(40) }), operator))),
    e => e.code === 'CONFLICT', 'a different commit won the race');
  assert.equal((await db.prepare('SELECT commit_sha FROM intake WHERE id = ?1').bind(conflicting.intake_id).first()).commit_sha, 'b'.repeat(40));

  const identical = await submitLesson(submitInput(), ctx);
  const retried = await transitionIntake(commit(identical.intake_id), racing(() => transitionIntake(commit(identical.intake_id), operator)));
  assert.deepEqual(retried, { intake_id: identical.intake_id, state: 'committed', duplicate: true }, 'an identical concurrent commit is a duplicate');

  await assert.rejects(
    transitionIntake({ action: 'ack', intake_id: conflicting.intake_id, revision, commit: 'b'.repeat(40), release_commit: 'c'.repeat(40) },
      racing(() => transitionIntake({ action: 'ack', intake_id: conflicting.intake_id, revision, commit: 'b'.repeat(40), release_commit: 'd'.repeat(40) }, operator))),
    e => e.code === 'CONFLICT', 'a different release ack won the race');

  const held = await submitLesson(submitInput(), ctx);
  await assert.rejects(
    transitionIntake({ action: 'hold', intake_id: held.intake_id, reason_code: 'policy_review' },
      racing(() => transitionIntake({ action: 'reject', intake_id: held.intake_id, reason_code: 'identifier' }, operator))),
    e => e.code === 'CONFLICT', 'a hold cannot resurrect a concurrently rejected intake');

  const rejected = await submitLesson(submitInput(), ctx);
  await assert.rejects(
    transitionIntake({ action: 'reject', intake_id: rejected.intake_id, reason_code: 'identifier' },
      racing(() => transitionIntake(commit(rejected.intake_id), operator))),
    e => e.code === 'CONFLICT', 'a non-terminal reject loses to a concurrent commit');
  assert.equal((await db.prepare('SELECT state FROM intake WHERE id = ?1').bind(rejected.intake_id).first()).state, 'committed');
});

test('evidence replacement loses to a concurrent validation and keeps the passed vote', async t => {
  const { db, operator } = await setup(t);
  const { ctx } = await submitContext(db, operator);
  const vote = { note_id: 'service/cas', revision: digest('t'), success_evidence: evidence() };
  await confirmLesson(vote, ctx);
  const event = await db.prepare("SELECT id, payload FROM events WHERE kind = 'confirm' AND note_id = ?1").bind(vote.note_id).first();
  await db.prepare('UPDATE events SET day = ? WHERE id = ?').bind(DAY - 1, event.id).run();
  const payloadDigest = JSON.parse(event.payload).payload_digest;
  const validate = () => transitionIntake({ action: 'validate_confirm', event_id: event.id, expected_payload_digest: payloadDigest, gate_digest: digest('u'), validation: 'passed' }, operator);
  const racing = { ...ctx, db: raceBefore(db, /^UPDATE events SET token_hash/, validate) };
  const result = await confirmLesson({ ...vote, success_evidence: evidence({ action: 'replacement' }) }, racing);
  assert.equal(result.validation, 'passed', 'the validation that won is reported, not overwritten');
  assert.equal(result.duplicate, true);
  const stored = JSON.parse((await db.prepare('SELECT payload FROM events WHERE id = ?1').bind(event.id).first()).payload);
  assert.equal(stored.validation, 'passed');
  assert.equal(stored.payload_digest, payloadDigest);
  assert.equal(stored.gate_digest, digest('u'));
});

test('input validation rejects oversized, malformed, and extra fields before any write', async t => {
  const { db, operator } = await setup(t);
  const { ctx } = await submitContext(db, operator);
  const before = (await db.prepare('SELECT count(*) AS n FROM intake').first()).n;
  const cases = [
    submitInput({ body: 'a'.repeat(2049) }),
    submitInput({ body: '€'.repeat(683) }),
    submitInput({ body: 'line\u0000nul' }),
    submitInput({ service_id: 'Not Valid' }),
    submitInput({ doc_url: 'http://docs.example.com/x' }),
    submitInput({ doc_url: 'https://docs.example.com/x?q=1' }),
    submitInput({ doc_url: 'https://docs.example.com/x#frag' }),
    submitInput({ doc_url: 'https://user:pw@docs.example.com/x' }),
    submitInput({ doc_url: 'https://%64ocs.example.com/x' }),
    submitInput({ success_evidence: { action: 'a' } }),
    submitInput({ success_evidence: { action: 'a', outcome: 'b', extra: 'c' } }),
    submitInput({ success_evidence: { action: '   ', outcome: 'ok' } }),
    submitInput({ request_id: 'not-a-uuid' }),
    submitInput({ kind: 'note' }),
    submitInput({ expected_revision: digest('n') }),
    { ...submitInput(), extra: true },
  ];
  for (const input of cases) await assert.rejects(submitLesson(input, ctx), e => e.code === 'INVALID_PARAMS', JSON.stringify(input).slice(0, 80));
  await assert.rejects(confirmLesson({ note_id: 'service/x', revision: 'sha256:short', success_evidence: evidence() }, ctx), e => e.code === 'INVALID_PARAMS');
  await assert.rejects(confirmLesson({ note_id: 'not-a-note', revision: digest('o'), success_evidence: evidence() }, ctx), e => e.code === 'INVALID_PARAMS');
  await assert.rejects(reportFailure({ note_id: 'service/x', revision: digest('p'), reason: '' }, ctx), e => e.code === 'INVALID_PARAMS');
  await assert.rejects(reportFailure({ note_id: 'service/x', revision: digest('p'), reason: 'r'.repeat(2049) }, ctx), e => e.code === 'INVALID_PARAMS');
  assert.equal((await db.prepare('SELECT count(*) AS n FROM intake').first()).n, before, 'rejected input never reaches D1');
  assert.equal((await db.prepare('SELECT count(*) AS n FROM events').first()).n, 0);
  const accepted = await submitLesson(submitInput({ body: 'a'.repeat(2048) }), ctx);
  assert.equal(accepted.duplicate, false, 'exactly 2048 bytes is accepted');
  await assert.rejects(submitLesson(submitInput(), { ...ctx, auth: { ...ctx.auth, scope: 'read' } }), e => e.code === 'FORBIDDEN');
  await assert.rejects(submitLesson(submitInput(), { ...ctx, org: 'company' }), e => e.code === 'FORBIDDEN');
});

test('an open intake queue of 1000 refuses new work with QUEUE_FULL', async t => {
  const { db, operator } = await setup(t);
  const { issued } = await submitContext(db, operator);
  const statements = [];
  for (let i = 0; i < 1000; i++) {
    statements.push(db.prepare(
      "INSERT INTO intake (id, token_hash, lineage_id, request_id, request_digest, kind, service_id, body, doc_url, success_evidence, state, created, updated, day) " +
      "VALUES (?1, ?2, ?3, ?4, ?5, 'procedure', ?6, NULL, NULL, NULL, 'pending', ?7, ?7, ?8)",
    ).bind(crypto.randomUUID(), issued.hash, issued.lineage_id, crypto.randomUUID(), 'd'.repeat(64), 'queued-' + i, NOW, DAY));
  }
  await db.batch(statements);
  const { ctx: fresh } = await submitContext(db, operator);
  await assert.rejects(submitLesson(submitInput(), fresh), e => e.code === 'QUEUE_FULL');
});

test('events can be filtered by kind across notes', async t => {
  const { db, operator } = await setup(t);
  const { ctx } = await submitContext(db, operator);
  const revision = digest('k');
  await confirmLesson({ note_id: 'service/kinds', revision, success_evidence: evidence({ action: 'ran it' }) }, ctx);
  await reportFailure({ note_id: 'service/kinds', revision, reason: 'broke' }, ctx);
  const reports = (await listEvents({ kind: 'report' }, operator)).items;
  assert.deepEqual(reports.map(item => item.kind), ['report']);
  await assert.rejects(listEvents({ kind: 'Report;' }, operator), e => e.code === 'INVALID_PARAMS');
});
