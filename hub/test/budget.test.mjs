import assert from 'node:assert/strict';
import test from 'node:test';
import { Miniflare } from 'miniflare';
import { budgetRequest, budgetSummary } from '../src/budget.ts';

// Tables are created here from the contract DDL (design §2.1 plus the budget_override
// table added to migrations/0001_intake.sql) so this file runs without sibling files.
const SCHEMA = [
`CREATE TABLE tokens (
  hash TEXT PRIMARY KEY CHECK(length(hash) = 64 AND hash NOT GLOB '*[^0-9a-f]*'),
  org TEXT NOT NULL,
  scope TEXT NOT NULL CHECK(scope IN ('read','submit','operator')),
  expires INTEGER NOT NULL,
  lineage_id TEXT NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1)),
  created INTEGER NOT NULL
)`,
`CREATE TABLE intake (
  id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL REFERENCES tokens(hash),
  lineage_id TEXT NOT NULL,
  request_id TEXT NOT NULL,
  request_digest TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('procedure','lesson')),
  service_id TEXT NOT NULL,
  target_note_id TEXT,
  expected_revision TEXT,
  body TEXT,
  state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','held','committed','published','rejected')),
  created INTEGER NOT NULL,
  updated INTEGER NOT NULL,
  day INTEGER NOT NULL
)`,
`CREATE TABLE events (
  id TEXT PRIMARY KEY,
  intake_id TEXT REFERENCES intake(id),
  note_id TEXT,
  revision TEXT,
  lineage_id TEXT NOT NULL,
  token_hash TEXT NOT NULL REFERENCES tokens(hash),
  kind TEXT NOT NULL CHECK(kind IN ('confirm','report','ack','cost','reservation')),
  action TEXT NOT NULL DEFAULT '',
  operation_id TEXT NOT NULL,
  value INTEGER NOT NULL DEFAULT 0 CHECK(value >= 0),
  payload TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(payload)),
  month TEXT,
  created INTEGER NOT NULL,
  day INTEGER NOT NULL,
  UNIQUE(kind, operation_id),
  CHECK(kind IN ('cost','reservation') OR intake_id IS NOT NULL OR (note_id IS NOT NULL AND revision IS NOT NULL)),
  CHECK(kind NOT IN ('confirm','report') OR (note_id IS NOT NULL AND revision IS NOT NULL)),
  CHECK(kind NOT IN ('cost','reservation') OR month IS NOT NULL)
)`,
`CREATE TABLE budget_override (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  cap_microusd INTEGER NOT NULL CHECK(cap_microusd >= 0),
  updated INTEGER NOT NULL
)`,
];

const ORG = 'public';
const HASH = 'a'.repeat(64);
const NOW = Date.UTC(2026, 8, 15, 0, 0, 0) / 1000; // 2026-09-15Z
const SEPT = '2026-09';
const DIGEST = `sha256:${'c'.repeat(64)}`;
const uuid = n => `${String(n).padStart(8, '0')}-0000-4000-8000-000000000000`;

async function harness(t) {
  const runtime = new Miniflare({
    telemetry: { enabled: false },
    workers: [{ config: {
      type: 'worker', name: 'budget', compatibilityDate: '2026-09-03',
      manifest: { mainModule: 'fixture.mjs', modules: { 'fixture.mjs': { type: 'esm', contents: 'export default {fetch(){return new Response("ok")}}' } } },
      env: { HUB_DB: { type: 'd1', name: 'HUB_DB' } }, exports: {},
    } }],
  });
  t.after(() => runtime.dispose());
  const db = await runtime.getD1Database('HUB_DB');
  for (const statement of SCHEMA) await db.prepare(statement).run();
  await db.prepare('INSERT INTO tokens (hash,org,scope,expires,lineage_id,revoked,created) VALUES (?,?,?,?,?,0,?)')
    .bind(HASH, ORG, 'operator', 2_000_000_000, uuid(9), NOW).run();
  return db;
}

const context = (db, overrides = {}) => ({
  db,
  auth: { hash: HASH, org: ORG, scope: 'operator', lineage_id: uuid(9), expires: 2_000_000_000 },
  org: ORG,
  cap: 1_000_000,
  now: NOW,
  ...overrides,
});

// The exact flat shape H3 Budget.reserve/ModelGateway._settle send (§A: no request_meta wrapper).
const requestMeta = () => ({ request_digest: `sha256:${'b'.repeat(64)}`, bytes: 1024 });
const reserveInput = (operation_id, reserve_micro_usd, extra = {}) => ({
  action: 'reserve', operation_id, purpose: 'bundle', target_digest: DIGEST, reserve_micro_usd,
  ...requestMeta(), ...extra,
});
const settleInput = (reservation_id, actual_micro_usd, extra = {}) => ({
  action: 'settle', reservation_id, actual_micro_usd, status: 'actual', ...extra,
});
const rejects = (promise, code, status) => assert.rejects(promise, e => {
  assert.equal(e.code, code, `expected ${code}, got ${e.code}/${e.message}`);
  assert.equal(e.status, status);
  return true;
});
const rows = async (db, sql, ...params) => {
  const result = await db.prepare(sql).bind(...params).all();
  return result.results;
};

test('reserve grants within the cap and records one allowlisted reservation', async t => {
  const db = await harness(t);
  const c = context(db);
  const grant = await budgetRequest(reserveInput(uuid(1), 400_000), c);
  assert.deepEqual(Object.keys(grant).sort(), ['expires_at', 'granted', 'month', 'reservation_id']);
  assert.equal(grant.granted, true);
  assert.equal(grant.month, SEPT);
  assert.equal(grant.expires_at, Date.UTC(2026, 9, 1) / 1000);
  assert.match(grant.reservation_id, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);

  const summary = await budgetSummary(c);
  assert.deepEqual(summary, { month: SEPT, cap_micro_usd: 1_000_000, cost_micro_usd: 0, reserved_micro_usd: 400_000, available_micro_usd: 600_000 });

  const stored = await rows(db, "SELECT payload, month, action, value FROM events WHERE kind='reservation'");
  assert.equal(stored.length, 1);
  assert.equal(stored[0].month, SEPT);
  assert.equal(stored[0].action, 'reserved');
  const payload = JSON.parse(stored[0].payload);
  assert.deepEqual(payload, { purpose: 'bundle', target_digest: DIGEST, ...requestMeta() });
});

test('a duplicate operation id never authorizes a second reservation', async t => {
  const db = await harness(t);
  const c = context(db);
  const first = await budgetRequest(reserveInput(uuid(1), 400_000), c);
  assert.equal(first.granted, true);
  assert.deepEqual(await budgetRequest(reserveInput(uuid(1), 400_000), c), { granted: false, duplicate: true });
  assert.deepEqual(await budgetRequest(reserveInput(uuid(1), 900_000), c), { granted: false, duplicate: true });
  assert.equal((await rows(db, "SELECT id FROM events WHERE kind='reservation'")).length, 1);
  assert.equal((await budgetSummary(c)).reserved_micro_usd, 400_000);
  await rejects(budgetRequest(reserveInput(uuid(3), 700_000), c), 'BUDGET_EXHAUSTED', 409);
});

test('reserve denies at the remaining-balance boundary', async t => {
  const db = await harness(t);
  const c = context(db);
  await budgetRequest(reserveInput(uuid(1), 600_000), c);
  const before = (await rows(db, "SELECT id FROM events")).length;
  await rejects(budgetRequest(reserveInput(uuid(2), 400_001), c), 'BUDGET_EXHAUSTED', 409);
  assert.equal((await rows(db, "SELECT id FROM events")).length, before, 'denied reserve writes nothing');
  assert.equal((await budgetRequest(reserveInput(uuid(3), 400_000), c)).granted, true);
  assert.equal((await budgetSummary(c)).available_micro_usd, 0);
});

test('concurrent reserves cannot both spend the same balance', async t => {
  const db = await harness(t);
  const c = context(db);
  const outcomes = await Promise.allSettled([
    budgetRequest(reserveInput(uuid(1), 600_000), c),
    budgetRequest(reserveInput(uuid(2), 600_000), c),
  ]);
  assert.equal(outcomes.filter(o => o.status === 'fulfilled').length, 1);
  assert.equal(outcomes.filter(o => o.status === 'rejected' && o.reason.code === 'BUDGET_EXHAUSTED').length, 1);
  assert.equal((await budgetSummary(c)).reserved_micro_usd, 600_000);
  assert.equal((await rows(db, "SELECT id FROM events WHERE kind='reservation'")).length, 1);
});

test('settle copies the reservation month across a rollover and zeroes the reservation', async t => {
  const db = await harness(t);
  const c = context(db);
  const grant = await budgetRequest(reserveInput(uuid(1), 500_000), c);
  const october = { ...c, now: Date.UTC(2026, 9, 5, 12, 0, 0) / 1000 };
  const settlement = await budgetRequest(settleInput(grant.reservation_id, 120_000), october);
  assert.deepEqual(Object.keys(settlement).sort(), ['actual_micro_usd', 'cost_id', 'duplicate', 'month', 'reservation_id', 'settled']);
  assert.equal(settlement.settled, true);
  assert.equal(settlement.reservation_id, grant.reservation_id);
  assert.equal(settlement.actual_micro_usd, 120_000);
  assert.equal(settlement.month, SEPT);
  assert.equal(settlement.duplicate, false);
  assert.match(settlement.cost_id, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);

  const cost = await rows(db, "SELECT month, value, operation_id FROM events WHERE kind='cost'");
  assert.deepEqual(cost.map(r => [r.month, r.value]), [[SEPT, 120_000]]);
  const reservation = await rows(db, "SELECT value, action, month FROM events WHERE kind='reservation'");
  assert.deepEqual(reservation.map(r => [r.value, r.action, r.month]), [[0, 'settled', SEPT]]);

  assert.deepEqual(await budgetSummary(c), { month: SEPT, cap_micro_usd: 1_000_000, cost_micro_usd: 120_000, reserved_micro_usd: 0, available_micro_usd: 880_000 });
  assert.deepEqual(await budgetSummary(october), { month: '2026-10', cap_micro_usd: 1_000_000, cost_micro_usd: 0, reserved_micro_usd: 0, available_micro_usd: 1_000_000 });
});

test('settlement replays are idempotent and a different amount conflicts', async t => {
  const db = await harness(t);
  const c = context(db);
  const grant = await budgetRequest(reserveInput(uuid(1), 500_000), c);
  const first = await budgetRequest(settleInput(grant.reservation_id, 100_000, {
    usage: { input_tokens: 10, output_tokens: 5 }, model_digest: DIGEST,
  }), c);
  assert.equal(first.duplicate, false);
  const stored = await rows(db, "SELECT payload FROM events WHERE kind = 'cost'");
  const costPayload = JSON.parse(stored[0].payload);
  assert.deepEqual(Object.keys(costPayload).sort(), ['bytes', 'model_digest', 'request_digest', 'reservation_id', 'status', 'usage']);
  assert.equal(costPayload.status, 'actual');
  assert.equal(costPayload.request_digest, requestMeta().request_digest, 'the reservation measurement is carried into the cost row');
  assert.equal(costPayload.bytes, requestMeta().bytes);
  assert.equal((await budgetRequest(settleInput(grant.reservation_id, 100_000), c)).duplicate, true);
  await rejects(budgetRequest(settleInput(grant.reservation_id, 101_000), c), 'SETTLEMENT_CONFLICT', 409);
  assert.equal((await rows(db, "SELECT id FROM events WHERE kind='cost'")).length, 1);
  assert.equal((await budgetSummary(c)).cost_micro_usd, 100_000);
  await rejects(budgetRequest(settleInput(uuid(7), 1), c), 'RESERVATION_NOT_FOUND', 404);
});

test('settlement above the reserved amount is rejected without touching state', async t => {
  const db = await harness(t);
  const c = context(db);
  const grant = await budgetRequest(reserveInput(uuid(1), 300_000), c);
  await rejects(budgetRequest(settleInput(grant.reservation_id, 300_001), c), 'SETTLEMENT_EXCEEDS_RESERVATION', 409);
  assert.equal((await rows(db, "SELECT id FROM events WHERE kind='cost'")).length, 0);
  assert.deepEqual((await rows(db, "SELECT value, action FROM events WHERE kind='reservation'")).map(r => [r.value, r.action]), [[300_000, 'reserved']]);
  assert.equal((await budgetRequest(settleInput(grant.reservation_id, 0), c)).settled, true);
  assert.equal((await budgetSummary(c)).cost_micro_usd, 0);
});

test('settle accepts the conservative_max status H3 sends when usage is unavailable', async t => {
  const db = await harness(t);
  const c = context(db);
  const grant = await budgetRequest(reserveInput(uuid(1), 300_000), c);
  const settlement = await budgetRequest({
    action: 'settle', reservation_id: grant.reservation_id, actual_micro_usd: 300_000,
    usage: {}, status: 'conservative_max',
  }, c);
  assert.equal(settlement.settled, true);
  assert.equal(JSON.parse((await rows(db, "SELECT payload FROM events WHERE kind='cost'"))[0].payload).status, 'conservative_max');
  assert.equal((await budgetSummary(c)).cost_micro_usd, 300_000);
});

test('an unused reservation settles at zero with status unused, idempotently', async t => {
  const db = await harness(t);
  const c = context(db);
  const grant = await budgetRequest(reserveInput(uuid(1), 300_000), c);
  const unused = extra => budgetRequest({ ...settleInput(grant.reservation_id, 0), status: 'unused', ...extra }, c);
  // A nonzero amount or nonzero usage means the call happened: 'unused' would hide real cost.
  await rejects(unused({ actual_micro_usd: 1 }), 'INVALID_PARAMS', 400);
  await rejects(unused({ usage: { input_tokens: 1 } }), 'INVALID_PARAMS', 400);
  assert.equal((await rows(db, "SELECT id FROM events WHERE kind='cost'")).length, 0);

  // The exact verify_hub teardown body.
  const first = await unused({ usage: {} });
  assert.equal(first.settled, true);
  assert.equal(first.duplicate, false);
  assert.equal(first.actual_micro_usd, 0);
  const payload = JSON.parse((await rows(db, "SELECT payload FROM events WHERE kind='cost'"))[0].payload);
  assert.equal(payload.status, 'unused');
  assert.equal(payload.request_digest, requestMeta().request_digest);
  assert.equal(payload.bytes, requestMeta().bytes);
  assert.equal((await unused({ usage: { input_tokens: 0 } })).duplicate, true);
  assert.equal((await unused({})).duplicate, true);
  assert.equal((await rows(db, "SELECT id FROM events WHERE kind='cost'")).length, 1);
  assert.deepEqual(await budgetSummary(c), { month: SEPT, cap_micro_usd: 1_000_000, cost_micro_usd: 0, reserved_micro_usd: 0, available_micro_usd: 1_000_000 });
});

test('override lowers the effective cap, rejects increases, and null restores the var', async t => {
  const db = await harness(t);
  const c = context(db);
  assert.deepEqual(await budgetRequest({ cap_microusd: 10_000 }, c), { cap_micro_usd: 10_000, override_micro_usd: 10_000 });
  assert.equal((await budgetSummary(c)).cap_micro_usd, 10_000);
  await budgetRequest(reserveInput(uuid(1), 10_000), c);
  await rejects(budgetRequest(reserveInput(uuid(2), 1), c), 'BUDGET_EXHAUSTED', 409);
  // Only the immutable var is a ceiling: an override may move up again while it stays below it.
  assert.deepEqual(await budgetRequest({ cap_microusd: 10_001 }, c), { cap_micro_usd: 10_001, override_micro_usd: 10_001 });
  assert.equal((await budgetRequest(reserveInput(uuid(2), 1), c)).granted, true);
  await rejects(budgetRequest({ cap_microusd: 1_000_001 }, c), 'INVALID_PARAMS', 400);
  await rejects(budgetRequest({ cap_microusd: -1 }, c), 'INVALID_PARAMS', 400);
  await rejects(budgetRequest({ cap_microusd: 10.5 }, c), 'INVALID_PARAMS', 400);
  assert.equal((await budgetSummary(c)).cap_micro_usd, 10_001, 'rejected increases leave the override alone');
  assert.deepEqual(await budgetRequest({ cap_microusd: null }, c), { cap_micro_usd: 1_000_000, override_micro_usd: null });
  assert.equal((await budgetSummary(c)).cap_micro_usd, 1_000_000);
  assert.equal((await budgetRequest(reserveInput(uuid(3), 500_000), c)).granted, true);
  assert.deepEqual(await budgetRequest({ cap_microusd: 1_000_000 }, c), { cap_micro_usd: 1_000_000, override_micro_usd: 1_000_000 });
});

test('a lowered override fixes the ceiling and available clamps at zero', async t => {
  const db = await harness(t);
  const c = context(db);
  await budgetRequest({ cap_microusd: 100_000 }, c);
  await budgetRequest(reserveInput(uuid(1), 90_000), c);
  const before = (await rows(db, "SELECT id FROM events")).length;
  await rejects(budgetRequest(reserveInput(uuid(2), 10_001), c), 'BUDGET_EXHAUSTED', 409);
  assert.equal((await rows(db, "SELECT id FROM events")).length, before, 'no spend after a lowered cap');
  assert.equal((await budgetRequest(reserveInput(uuid(3), 10_000), c)).granted, true, 'the exact remainder is still granted');
  assert.deepEqual(await budgetSummary(c), { month: SEPT, cap_micro_usd: 100_000, cost_micro_usd: 0, reserved_micro_usd: 100_000, available_micro_usd: 0 });
  await rejects(budgetRequest(reserveInput(uuid(5), 1), c), 'BUDGET_EXHAUSTED', 409);
  await budgetRequest({ cap_microusd: 10_000 }, c);
  const clamped = await budgetSummary(c);
  assert.equal(clamped.cap_micro_usd, 10_000);
  assert.equal(clamped.available_micro_usd, 0, 'available never goes below zero');
  await rejects(budgetRequest(reserveInput(uuid(4), 1), c), 'BUDGET_EXHAUSTED', 409);
});

test('an override lowered between request start and the reservation write still binds it', async t => {
  const db = await harness(t);
  const c = context(db);
  // Another operator's POST /admin/budget completes after this reserve began but before its
  // INSERT runs. The reserve must see the lowered cap, not a value it read earlier.
  const interleaved = new Proxy(db, {
    get(target, key) {
      if (key !== 'prepare') return Reflect.get(target, key);
      return sql => {
        const statement = target.prepare(sql);
        if (!/INSERT INTO events/.test(sql) || !/'reservation'/.test(sql)) return statement;
        return { bind: (...params) => ({
          first: async () => {
            await budgetRequest({ cap_microusd: 10_000 }, context(db));
            return statement.bind(...params).first();
          },
        }) };
      };
    },
  });
  await rejects(budgetRequest(reserveInput(uuid(1), 500_000), { ...c, db: interleaved }), 'BUDGET_EXHAUSTED', 409);
  assert.equal((await rows(db, "SELECT id FROM events WHERE kind='reservation'")).length, 0, 'no grant above the lowered cap');
  assert.equal((await budgetSummary(c)).cap_micro_usd, 10_000);
});

test('concurrent override lowering and reserve never leave a grant above the lowered cap', async t => {
  const db = await harness(t);
  const c = context(db);
  const [grant] = await Promise.allSettled([
    budgetRequest(reserveInput(uuid(1), 500_000), c),
    budgetRequest({ cap_microusd: 10_000 }, c),
  ]);
  const reserved = (await budgetSummary(c)).reserved_micro_usd;
  // Either the reserve serialized first (granted under the old cap) or it saw the new one.
  if (grant.status === 'fulfilled') assert.equal(reserved, 500_000);
  else assert.equal(reserved, 0);
  await rejects(budgetRequest(reserveInput(uuid(2), 1), c), 'BUDGET_EXHAUSTED', 409);
});

test('rejects unknown fields, unsafe metadata, bad bounds and wrong scope', async t => {
  const db = await harness(t);
  const c = context(db);
  const bad = async (input, code = 'INVALID_PARAMS', status = 400) => rejects(budgetRequest(input, c), code, status);

  await bad({ ...reserveInput(uuid(1), 1_000), extra: 'x' });
  await bad({ ...reserveInput(uuid(1), 1_000), purpose: 'watch' });
  await bad({ ...reserveInput(uuid(1), 1_000), operation_id: 'not-a-uuid' });
  await bad({ ...reserveInput(uuid(1), 1_000), target_digest: 'sha256:zz' });
  await bad({ ...reserveInput(uuid(1), 1_000), target_digest: 64 });
  await bad({ ...reserveInput(uuid(1), 1_000), reserve_micro_usd: 0 });
  await bad({ ...reserveInput(uuid(1), 1_000), reserve_micro_usd: -1 });
  await bad({ ...reserveInput(uuid(1), 1_000), reserve_micro_usd: 1.5 });
  await bad({ ...reserveInput(uuid(1), 1_000), reserve_micro_usd: 10_000_001 });
  await bad({ ...reserveInput(uuid(1), 1_000), secret_match_count: 1 });
  await bad({ ...reserveInput(uuid(1), 1_000), run_id: uuid(2) });
  await bad({ ...reserveInput(uuid(1), 1_000), bytes: 16_777_217 });
  await bad({ ...reserveInput(uuid(1), 1_000), bytes: -1 });
  await bad({ ...reserveInput(uuid(1), 1_000), request_digest: 'free text' });
  await bad({ ...reserveInput(uuid(1), 1_000), bytes: undefined });
  await bad({ ...reserveInput(uuid(1), 1_000), request_digest: undefined });
  await bad({ action: 'reserve', operation_id: uuid(1), purpose: 'bundle', target_digest: DIGEST, reserve_micro_usd: 1_000, request_meta: requestMeta() });
  await bad({ ...reserveInput(uuid(1), 1_000), note_id: 'service/x' });
  await bad({ ...reserveInput(uuid(1), 1_000), purpose: 'intake' });
  await bad({ ...reserveInput(uuid(1), 1_000), purpose: 'confirm' });
  await bad({ ...reserveInput(uuid(1), 1_000), purpose: 'confirm', intake_id: uuid(20) });
  await bad({ action: 'mark_uncertain', reservation_id: uuid(7), status: 'transport_unknown' });
  await bad(settleInput(uuid(7), 1_000, { status: 'unknown' }));
  await bad(settleInput(uuid(7), 1_000, { status: 'reported' }));
  await bad(settleInput(uuid(7), 1_000, { status: 'estimated' }));
  await bad({ ...settleInput(uuid(7), 1_000), usage: { input_tokens: -1 } });
  await bad({ ...settleInput(uuid(7), 1_000), usage: { 'bad key': 1 } });
  await bad({ ...settleInput(uuid(7), 1_000), provider_request_id: 'req_1' });
  await bad({ ...settleInput(uuid(7), 1_000), request_digest: requestMeta().request_digest });
  await bad({ ...settleInput(uuid(7), 1_000), actual_micro_usd: 10_000_001 });
  await bad({ cap_microusd: 10_000, action: 'reserve' });
  await bad({ nothing: true });

  await rejects(budgetRequest(reserveInput(uuid(1), 1_000), context(db, { auth: { ...c.auth, scope: 'read' } })), 'FORBIDDEN', 403);
  await rejects(budgetRequest(reserveInput(uuid(1), 1_000), context(db, { auth: { ...c.auth, org: 'company' } })), 'FORBIDDEN', 403);
  await rejects(budgetSummary(context(db, { auth: { ...c.auth, expires: NOW - 1 } })), 'UNAUTHORIZED', 401);
  await rejects(budgetSummary(context(db, { cap: 10_000_001 })), 'CONFIG_INVALID', 500);

  await db.prepare('UPDATE tokens SET revoked = 1 WHERE hash = ?').bind(HASH).run();
  await rejects(budgetRequest(reserveInput(uuid(1), 1_000), c), 'UNAUTHORIZED', 401);
  assert.equal((await rows(db, "SELECT id FROM events")).length, 0, 'no audit row is stored for a rejected request');
});

test('reserve targets are recorded and the intake purpose needs its intake', async t => {
  const db = await harness(t);
  const c = context(db);
  await db.prepare('INSERT INTO intake (id,token_hash,lineage_id,request_id,request_digest,kind,service_id,state,created,updated,day) VALUES (?,?,?,?,?,?,?,?,?,?,?)')
    .bind(uuid(20), HASH, uuid(9), uuid(21), DIGEST, 'lesson', 'example', 'pending', NOW, NOW, Math.floor(NOW / 86400)).run();
  const input = reserveInput(uuid(1), 1_000, {
    purpose: 'intake', intake_id: uuid(20), note_id: 'service/example', revision: `sha256:${'d'.repeat(64)}`,
  });
  const grant = await budgetRequest(input, c);
  assert.equal(grant.granted, true);
  assert.deepEqual((await rows(db, 'SELECT intake_id, note_id, revision FROM events WHERE kind = ?', 'reservation'))
    .map(r => [r.intake_id, r.note_id, r.revision]), [[uuid(20), 'service/example', `sha256:${'d'.repeat(64)}`]]);
  const bad = { ...input, note_id: 'service/..', revision: `sha256:${'d'.repeat(64)}` };
  await rejects(budgetRequest({ ...bad, operation_id: uuid(4) }, c), 'INVALID_PARAMS', 400);
  await rejects(budgetRequest({ ...input, operation_id: uuid(5), intake_id: 'not-a-uuid' }, c), 'INVALID_PARAMS', 400);
});

test('a confirmation reserves against its note revision without an intake id', async t => {
  const db = await harness(t);
  const c = context(db);
  const revision = `sha256:${'d'.repeat(64)}`;
  // Exactly what H3 _confirm_validation sends: purpose, note_id, revision, target_digest, no intake_id.
  const grant = await budgetRequest(reserveInput(uuid(1), 1_000, {
    purpose: 'confirm', target_digest: revision, note_id: 'service/example', revision,
  }), c);
  assert.equal(grant.granted, true);
  assert.deepEqual((await rows(db, "SELECT intake_id, note_id, revision FROM events WHERE kind = 'reservation'"))
    .map(r => [r.intake_id, r.note_id, r.revision]), [[null, 'service/example', revision]]);
});
