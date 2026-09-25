// Hub budget ledger: GET/POST /admin/budget (design §2.4, §3.3, §A-4).
//
// Amounts are integer microUSD. Every spend decision is made by one conditional D1
// statement (or one D1 batch) so two callers can never authorize the same balance.
// The Worker never trusts a caller for the month, the cap or the reservation amount.
//
// Caller contract (index.ts):
//   budgetSummary(context)        -> BudgetSummary
//   budgetRequest(input, context) -> BudgetGrant | BudgetDuplicate | BudgetSettlement | BudgetOverride
//   context = { db, auth, org, cap, now? }   (now: unix seconds, tests only)
// All rejections are HubError with a fixed code from auth.ts.

import type { ErrorCode } from "./auth.ts";
import { assertPlainObject, HASH_RE, HubError, requireScope } from "./auth.ts";
import type { DbContext } from "./auth.ts";

/** `cap` is the validated HUB_MONTHLY_CAP_MICROUSD var; `now` exists only for deterministic tests. */
export type BudgetContext = DbContext & { cap: number; now?: number };

export type BudgetSummary = {
  month: string;
  cap_micro_usd: number;
  cost_micro_usd: number;
  reserved_micro_usd: number;
  available_micro_usd: number;
};

export type BudgetGrant = { granted: true; reservation_id: string; month: string; expires_at: number };
export type BudgetDuplicate = { granted: false; duplicate: true };
export type BudgetSettlement = {
  settled: true;
  reservation_id: string;
  cost_id: string;
  actual_micro_usd: number;
  month: string;
  duplicate: boolean;
};
export type BudgetOverride = { cap_micro_usd: number; override_micro_usd: number | null };
export type BudgetRequestResult = BudgetGrant | BudgetDuplicate | BudgetSettlement | BudgetOverride;

/** Hard immutable ceiling for a Hub deployment (design §2.4: public/company = $10). */
export const MAX_CAP_MICRO_USD = 10_000_000;

const MAX_REQUEST_META_BYTES = 16_777_216;
const MAX_USAGE_VALUE = 1_000_000_000;
const MAX_USAGE_FIELDS = 16;
const SECONDS_PER_DAY = 86_400;
const MAX_UNIX_SECONDS = 4_102_444_800;

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SHA256_RE = /^sha256:[0-9a-f]{64}$/;
const BARE_SHA256_RE = /^[0-9a-f]{64}$/;
const NOTE_ID_RE = /^(service|failure)\/[a-z0-9][a-z0-9._-]*$/;
const USAGE_KEY_RE = /^[a-z][a-z0-9_]{0,31}$/;

// §A: the cost measurement is the flat request_digest + bytes pair only: no request_meta
// wrapper, run id, operation id, secret-match counter or provider request id.
const RESERVE_KEYS = [
  "action", "operation_id", "purpose", "target_digest",
  "intake_id", "note_id", "revision", "reserve_micro_usd", "request_digest", "bytes",
];
const SETTLE_KEYS = [
  "action", "reservation_id", "actual_micro_usd", "status", "usage",
  "model_digest", "pricing_digest",
];
// H3 ModelGateway: provider-reported usage, or the reserved maximum when usage is missing.
// 'unused' releases a reservation whose call never happened (verify_hub teardown): zero only.
const SETTLE_STATUSES = ["actual", "conservative_max", "unused"];

type Row = Record<string, unknown>;

function integerIn(value: unknown, low: number, high: number, code: ErrorCode = "INVALID_PARAMS"): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < low || value > high) throw new HubError(code);
  return value;
}

function uuidOf(value: unknown): string {
  if (typeof value !== "string" || !UUID_RE.test(value)) throw new HubError("INVALID_PARAMS");
  return value;
}

function digestOf(value: unknown): string {
  if (typeof value === "string" && SHA256_RE.test(value)) return value;
  if (typeof value === "string" && BARE_SHA256_RE.test(value)) return `sha256:${value}`;
  throw new HubError("INVALID_PARAMS");
}

/** UTC `YYYY-MM` of a unix second value. */
function monthOf(nowSeconds: number): string {
  const date = new Date(nowSeconds * 1000);
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;
}

/** Exclusive upper bound of a `YYYY-MM` month as unix seconds (the grant's expires_at). */
function endOfMonth(month: string): number {
  const [year, index] = month.split("-").map(Number);
  return Date.UTC(year, index, 1) / 1000;
}

type Session = { hash: string; org: string; now: number; day: number; month: string };

// The router authenticates /admin/*, but the token is re-checked against D1 here so these
// functions stay correct when called directly and a revoked token never spends.
async function openSession(context: BudgetContext): Promise<Session> {
  requireScope(context, ["operator"]);
  integerIn(context.cap, 0, MAX_CAP_MICRO_USD, "CONFIG_INVALID");
  if (!HASH_RE.test(context.auth.hash)) throw new HubError("UNAUTHORIZED");
  const now = context.now === undefined
    ? Math.floor(Date.now() / 1000)
    : integerIn(context.now, 1, MAX_UNIX_SECONDS, "CONFIG_INVALID");
  if (!Number.isSafeInteger(context.auth.expires) || context.auth.expires <= now) throw new HubError("UNAUTHORIZED");

  const token = await context.db
    .prepare("SELECT org, scope, revoked, expires FROM tokens WHERE hash = ?")
    .bind(context.auth.hash)
    .first();
  if (!token || token.org !== context.org || token.scope !== "operator" || token.revoked !== 0) {
    throw new HubError("UNAUTHORIZED");
  }
  if (Number(token.expires) <= now) throw new HubError("UNAUTHORIZED");

  return { hash: context.auth.hash, org: context.org, now, day: Math.floor(now / SECONDS_PER_DAY), month: monthOf(now) };
}

/** Effective cap = min(Worker var, D1 override). The override can only lower it. Reads only;
 *  reserve() recomputes it inside its conditional INSERT so a concurrent lowering binds. */
async function effectiveCap(context: BudgetContext): Promise<number> {
  const row = await context.db.prepare("SELECT cap_microusd FROM budget_override WHERE id = 1").first();
  if (!row) return context.cap;
  const override = integerIn(row.cap_microusd, 0, MAX_CAP_MICRO_USD, "CONFIG_INVALID");
  return Math.min(context.cap, override);
}

export async function budgetSummary(context: BudgetContext): Promise<BudgetSummary> {
  const session = await openSession(context);
  const cap = await effectiveCap(context);
  const row = await context.db.prepare(
    `SELECT
       COALESCE((SELECT SUM(value) FROM events WHERE month = ? AND kind = 'cost'), 0) AS cost,
       COALESCE((SELECT SUM(value) FROM events WHERE month = ? AND kind = 'reservation'), 0) AS reserved`,
  ).bind(session.month, session.month).first();
  const cost = Number(row?.cost ?? 0);
  const reserved = Number(row?.reserved ?? 0);
  return {
    month: session.month,
    cap_micro_usd: cap,
    cost_micro_usd: cost,
    reserved_micro_usd: reserved,
    available_micro_usd: Math.max(0, cap - cost - reserved),
  };
}

export async function budgetRequest(input: unknown, context: BudgetContext): Promise<BudgetRequestResult> {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new HubError("INVALID_PARAMS");
  const shape = input as Row;
  if (shape.action === "reserve") {
    return reserve(assertPlainObject(shape, RESERVE_KEYS), await openSession(context), context);
  }
  if (shape.action === "settle") {
    return settle(assertPlainObject(shape, SETTLE_KEYS), await openSession(context), context);
  }
  // §A-4 override: the only body without an action, and the only writer of budget_override.
  if (shape.action === undefined && Object.prototype.hasOwnProperty.call(shape, "cap_microusd")) {
    return setOverride(assertPlainObject(shape, ["cap_microusd"]), await openSession(context), context);
  }
  throw new HubError("INVALID_PARAMS");
}

/** Request measurement: digest and size only, both bounded and secret-free. Required on reserve. */
function requestMeasurement(body: Row): { request_digest: string; bytes: number } {
  return { request_digest: digestOf(body.request_digest), bytes: integerIn(body.bytes, 0, MAX_REQUEST_META_BYTES) };
}

/** Token counts only: bounded integer fields, no strings that could carry secrets. */
function usageOf(value: unknown): Row | undefined {
  if (value === undefined) return undefined;
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new HubError("INVALID_PARAMS");
  const entries = Object.entries(value as Row);
  if (entries.length > MAX_USAGE_FIELDS) throw new HubError("INVALID_PARAMS");
  const usage: Row = {};
  for (const [key, number] of entries) {
    if (!USAGE_KEY_RE.test(key)) throw new HubError("INVALID_PARAMS");
    usage[key] = integerIn(number, 0, MAX_USAGE_VALUE);
  }
  return usage;
}

async function reserve(body: Row, session: Session, context: BudgetContext): Promise<BudgetGrant | BudgetDuplicate> {
  const operation_id = uuidOf(body.operation_id);
  const purpose = body.purpose;
  if (purpose !== "intake" && purpose !== "confirm" && purpose !== "bundle") throw new HubError("INVALID_PARAMS");
  const reserve_micro_usd = integerIn(body.reserve_micro_usd, 1, MAX_CAP_MICRO_USD);
  const target_digest = digestOf(body.target_digest);
  const intake_id = body.intake_id === undefined ? null : uuidOf(body.intake_id);
  const revision = body.revision === undefined ? null : digestOf(body.revision);
  let note_id: string | null = null;
  if (body.note_id !== undefined) {
    if (typeof body.note_id !== "string" || body.note_id.length > 128 || !NOTE_ID_RE.test(body.note_id)) {
      throw new HubError("INVALID_PARAMS");
    }
    note_id = body.note_id;
  }
  if ((note_id === null) !== (revision === null)) throw new HubError("INVALID_PARAMS");
  // An intake reservation belongs to its intake row; a confirmation targets an already
  // published note revision and has no intake row of its own.
  if (purpose === "intake" && intake_id === null) throw new HubError("INVALID_PARAMS");
  if (purpose === "confirm" && note_id === null) throw new HubError("INVALID_PARAMS");
  const measurement = requestMeasurement(body);

  const id = crypto.randomUUID();
  const payload = JSON.stringify({ purpose, target_digest, ...measurement });
  // The effective cap is read in the same statement as the balance check: an override
  // committed before this INSERT always binds it, never a value read earlier.
  const inserted = await context.db.prepare(
    `INSERT INTO events
       (id, intake_id, note_id, revision, lineage_id, token_hash, kind, action,
        operation_id, value, payload, month, created, day)
     SELECT ?, ?, ?, ?, t.lineage_id, t.hash, 'reservation', 'reserved', ?, ?, ?, ?, ?, ?
     FROM tokens AS t,
          (SELECT MIN(?, COALESCE((SELECT cap_microusd FROM budget_override WHERE id = 1), ?)) AS cap) AS b
     WHERE t.hash = ? AND t.org = ? AND t.scope = 'operator'
       AND t.revoked = 0 AND t.expires > ?
       AND ? > 0 AND ? <= b.cap
       AND COALESCE((SELECT SUM(value) FROM events WHERE month = ? AND kind IN ('cost','reservation')), 0) + ? <= b.cap
     ON CONFLICT(kind, operation_id) DO NOTHING
     RETURNING id, value`,
  ).bind(
    id, intake_id, note_id, revision,
    operation_id, reserve_micro_usd, payload, session.month, session.now, session.day,
    context.cap, context.cap,
    session.hash, session.org, session.now,
    reserve_micro_usd, reserve_micro_usd,
    session.month, reserve_micro_usd,
  ).first();

  if (inserted) {
    return { granted: true, reservation_id: String(inserted.id), month: session.month, expires_at: endOfMonth(session.month) };
  }
  // The insert wrote nothing: either this operation id was already reserved (never a second
  // authorization) or the balance is gone. A missing or revoked token failed in openSession.
  const duplicate = await context.db
    .prepare("SELECT id FROM events WHERE kind = 'reservation' AND operation_id = ?")
    .bind(operation_id)
    .first();
  if (duplicate) return { granted: false, duplicate: true };
  throw new HubError("BUDGET_EXHAUSTED");
}

async function settle(body: Row, session: Session, context: BudgetContext): Promise<BudgetSettlement> {
  const reservation_id = uuidOf(body.reservation_id);
  const actual_micro_usd = integerIn(body.actual_micro_usd, 0, MAX_CAP_MICRO_USD);
  if (!SETTLE_STATUSES.includes(body.status as string)) throw new HubError("INVALID_PARAMS");
  const usage = usageOf(body.usage);
  if (body.status === "unused" && (actual_micro_usd !== 0 || Object.values(usage ?? {}).some(n => n !== 0))) {
    throw new HubError("INVALID_PARAMS");
  }
  const model_digest = body.model_digest === undefined ? undefined : digestOf(body.model_digest);
  const pricing_digest = body.pricing_digest === undefined ? undefined : digestOf(body.pricing_digest);

  const reservation = await context.db
    .prepare("SELECT id, operation_id, value, action, month, payload FROM events WHERE id = ? AND kind = 'reservation'")
    .bind(reservation_id)
    .first();
  if (!reservation) throw new HubError("RESERVATION_NOT_FOUND");

  // Carry the reservation's measurement into the cost row; a malformed legacy payload carries none.
  let measurement: Row = {};
  try {
    measurement = requestMeasurement(JSON.parse(String(reservation.payload)) as Row);
  } catch {
    measurement = {};
  }

  const cost_payload = JSON.stringify({
    reservation_id,
    status: body.status,
    usage,
    model_digest,
    pricing_digest,
    ...measurement,
  });
  const cost_id = crypto.randomUUID();
  // One batch: the cost row copies the reservation's month (even when the settlement lands in
  // a later month) and the reservation is zeroed only once its matching cost row exists.
  // An amount above the reservation never inserts and never zeroes.
  await context.db.batch([
    context.db.prepare(
      `INSERT INTO events
         (id, intake_id, note_id, revision, lineage_id, token_hash, kind, action,
          operation_id, value, payload, month, created, day)
       SELECT ?, r.intake_id, r.note_id, r.revision, r.lineage_id, r.token_hash, 'cost', 'settled',
              r.operation_id, ?, ?, r.month, ?, ?
       FROM events AS r
       WHERE r.id = ? AND r.kind = 'reservation' AND r.action = 'reserved'
         AND ? >= 0 AND ? <= r.value
       ON CONFLICT(kind, operation_id) DO NOTHING`,
    ).bind(cost_id, actual_micro_usd, cost_payload, session.now, session.day, reservation_id, actual_micro_usd, actual_micro_usd),
    context.db.prepare(
      `UPDATE events AS r SET value = 0, action = 'settled'
       WHERE r.id = ? AND r.kind = 'reservation' AND r.action = 'reserved'
         AND EXISTS (SELECT 1 FROM events AS c
                     WHERE c.kind = 'cost' AND c.operation_id = r.operation_id
                       AND c.value = ? AND c.month = r.month)`,
    ).bind(reservation_id, actual_micro_usd),
  ]);

  const cost = await context.db
    .prepare("SELECT id, value, month FROM events WHERE kind = 'cost' AND operation_id = ?")
    .bind(reservation.operation_id)
    .first();
  if (cost) {
    if (Number(cost.value) !== actual_micro_usd) throw new HubError("SETTLEMENT_CONFLICT");
    return {
      settled: true,
      reservation_id,
      cost_id: String(cost.id),
      actual_micro_usd,
      month: String(cost.month),
      duplicate: reservation.action === "settled",
    };
  }
  if (reservation.action === "reserved" && actual_micro_usd > Number(reservation.value)) {
    throw new HubError("SETTLEMENT_EXCEEDS_RESERVATION");
  }
  throw new HubError("SETTLEMENT_CONFLICT");
}

async function setOverride(body: Row, session: Session, context: BudgetContext): Promise<BudgetOverride> {
  const value = body.cap_microusd;
  if (value === null) {
    await context.db.prepare("DELETE FROM budget_override WHERE id = 1").run();
    return { cap_micro_usd: context.cap, override_micro_usd: null };
  }
  // The override may only lower the immutable Worker var, never raise it.
  const override = integerIn(value, 0, MAX_CAP_MICRO_USD);
  if (override > context.cap) throw new HubError("INVALID_PARAMS");
  await context.db.prepare(
    `INSERT INTO budget_override (id, cap_microusd, updated) VALUES (1, ?, ?)
     ON CONFLICT(id) DO UPDATE SET cap_microusd = excluded.cap_microusd, updated = excluded.updated`,
  ).bind(override, session.now).run();
  return { cap_micro_usd: Math.min(context.cap, override), override_micro_usd: override };
}
