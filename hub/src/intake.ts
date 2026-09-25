import { assertPlainObject, canonicalJson, HubError, requireScope, sha256Hex } from "./auth.ts";
import type { DbContext } from "./auth.ts";

const SERVICE_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const REVISION_RE = /^sha256:[0-9a-f]{64}$/;
const DIGEST_RE = /^[0-9a-f]{64}$/;
const GATE_RE = /^(?:sha256:)?[0-9a-f]{64}$/;
const NOTE_ID_RE = /^(?:service|failure)\/[a-z0-9]+(?:-[a-z0-9]+)*$/;
const COMMIT_RE = /^[0-9a-f]{40}$/;
const REASON_RE = /^[a-z][a-z0-9_]{0,63}$/;
const TERMINAL_REASONS: Record<string, true> = { superseded: true, recalled_before_publish: true, expired: true };
const INTENT_STATES: Record<string, true> = { pending: true, held: true, committed: true };
const MAX_TEXT_BYTES = 2048;

export type Evidence = { action: string; outcome: string };
export type SubmitInput = {
  request_id: string; service_id: string; kind: "procedure" | "lesson";
  body: string; doc_url: string; success_evidence: Evidence;
  note_id?: string; expected_revision?: string;
};
export type SubmitReceipt = { intake_id: string; state: string; duplicate: boolean };
export type ConfirmInput = { note_id: string; revision: string; success_evidence: Evidence };
export type ConfirmReceipt = {
  note_id: string; revision: string; accepted: true; duplicate: boolean;
  validation: "pending" | "passed" | "held" | "rejected";
};
export type ReportInput = { note_id: string; revision: string; reason: string };
export type ReportReceipt = { note_id: string; revision: string; accepted: true; duplicate: boolean };
export type IntakeItem = {
  id: string; token_hash: string; lineage_id: string; request_id: string; request_digest: string;
  kind: string; service_id: string; target_note_id: string | null; expected_revision: string | null;
  body: string | null; doc_url: string | null; success_evidence: Evidence | null;
  state: string; reason_code: string | null; note_id: string | null; revision: string | null;
  gate_digest: string | null; commit_sha: string | null; published_commit: string | null;
  created: number; updated: number; day: number; eligible: boolean;
};
export type EventItem = {
  id: string; intake_id: string | null; note_id: string | null; revision: string | null;
  lineage_id: string; token_hash: string; kind: string; action: string; operation_id: string;
  value: number; payload: Record<string, unknown>; month: string | null;
  created: number; day: number; eligible: boolean;
};
export type ListReceipt<T> = { items: T[]; next_cursor: string | null };
export type TransitionInput =
  | { action: "hold"; intake_id: string; reason_code: string; gate_digest?: string }
  | { action: "reject"; intake_id: string; reason_code: string; gate_digest?: string }
  | { action: "committed"; intake_id: string; note_id: string; revision: string; gate_digest: string; commit: string }
  | { action: "ack"; intake_id: string; revision: string; commit: string; release_commit: string }
  | { action: "review"; note_id: string; revision: string; decision: "promote" | "recall"; gate_digest: string; events_digest: string; request_id: string }
  | { action: "validate_confirm"; event_id: string; expected_payload_digest: string; gate_digest: string; validation: "passed" | "held" | "rejected" }
  | { action: "redact_report"; event_id: string; expected_payload_digest: string };

function invalid(): HubError {
  return new HubError("INVALID_PARAMS");
}

/** Free-text field: string, no NUL, 1..2048 UTF-8 bytes. JSON Schema maxLength is not a byte limit. */
function assertText(value: unknown, max = MAX_TEXT_BYTES): string {
  if (typeof value !== "string" || value.includes("\u0000") || value.length === 0) throw invalid();
  if (new TextEncoder().encode(value).byteLength > max) throw invalid();
  return value;
}

function assertUuid(value: unknown): string {
  if (typeof value !== "string" || !UUID_RE.test(value)) throw invalid();
  return value;
}

function assertServiceId(value: unknown): string {
  if (typeof value !== "string" || value.length > 64 || !SERVICE_RE.test(value)) throw invalid();
  return value;
}

function assertNoteId(value: unknown): string {
  if (typeof value !== "string" || value.length > 160 || !NOTE_ID_RE.test(value)) throw invalid();
  return value;
}

function assertRevision(value: unknown): string {
  if (typeof value !== "string" || !REVISION_RE.test(value)) throw invalid();
  return value;
}

function assertDigest(value: unknown): string {
  if (typeof value !== "string" || !DIGEST_RE.test(value)) throw invalid();
  return value;
}

function assertGateDigest(value: unknown): string {
  if (typeof value !== "string" || !GATE_RE.test(value)) throw invalid();
  return value;
}

function assertEvidence(value: unknown): Evidence {
  const params = assertPlainObject(value, ["action", "outcome"]);
  if (params.action === undefined || params.outcome === undefined) throw invalid();
  const fields = [params.action, params.outcome].map(field => {
    if (typeof field !== "string" || field.includes("\u0000")) throw invalid();
    return field.trim();
  });
  if (fields.some(field => field.length === 0 || new TextEncoder().encode(field).byteLength > MAX_TEXT_BYTES)) throw invalid();
  return { action: fields[0], outcome: fields[1] };
}

/** HTTPS, no query, fragment, userinfo, or percent-encoded authority. */
function assertDocUrl(value: unknown): string {
  const text = assertText(value);
  if (!/^https:\/\//.test(text)) throw invalid();
  const authority = /^https:\/\/([^/?#]*)/.exec(text)?.[1] ?? "";
  if (authority.length === 0 || authority.includes("@") || authority.includes("%")) throw invalid();
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    throw invalid();
  }
  if (url.protocol !== "https:" || url.username !== "" || url.password !== "" || url.search !== "" || url.hash !== "") throw invalid();
  if (url.hostname.length === 0 || url.hostname.includes("..")) throw invalid();
  return text;
}

function assertReasonCode(value: unknown): string {
  if (typeof value !== "string" || !REASON_RE.test(value)) throw invalid();
  return value;
}

function assertCommit(value: unknown): string {
  if (typeof value !== "string" || !COMMIT_RE.test(value)) throw invalid();
  return value;
}

function assertLimit(value: unknown, fallback: number, max: number): number {
  if (value === undefined) return fallback;
  if (!Number.isInteger(value) || (value as number) < 1 || (value as number) > max) throw invalid();
  return value as number;
}

function encodeCursor(created: number, id: string): string {
  return btoa(`${created}:${id}`).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function decodeCursor(value: unknown): { created: number; id: string } | null {
  if (value === undefined) return null;
  if (typeof value !== "string" || value.length === 0 || value.length > 256) throw invalid();
  const padded = value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (value.length % 4)) % 4);
  let decoded: string;
  try {
    decoded = atob(padded);
  } catch {
    throw invalid();
  }
  const split = decoded.indexOf(":");
  if (split <= 0) throw invalid();
  const created = Number(decoded.slice(0, split));
  const id = decoded.slice(split + 1);
  if (!Number.isInteger(created) || created < 0 || id.length === 0) throw invalid();
  return { created, id };
}

async function returningId(db: D1Database, sql: string, params: unknown[]): Promise<string | null> {
  const result = await db.prepare(sql).bind(...params).all<{ id: string }>();
  return result.results?.[0]?.id ?? null;
}

function unixNow(): number {
  return Math.floor(Date.now() / 1000);
}

/** Used when a conditional INSERT/UPDATE matched nothing: distinguish revoked/expired auth from limits. */
async function requireLiveToken(db: D1Database, hash: string, org: string, now: number): Promise<void> {
  const token = await db.prepare(
    "SELECT hash FROM tokens WHERE hash = ?1 AND org = ?2 AND scope IN ('submit','operator') AND revoked = 0 AND expires > ?3",
  ).bind(hash, org, now).first<{ hash: string }>();
  if (!token) throw new HubError("UNAUTHORIZED");
}

const SUBMIT_SQL =
  "INSERT INTO intake " +
  "(id, token_hash, lineage_id, request_id, request_digest, kind, service_id, target_note_id, expected_revision, body, doc_url, success_evidence, created, updated, day) " +
  "SELECT ?1, t.hash, t.lineage_id, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?11, ?12 " +
  "FROM tokens AS t WHERE t.hash = ?13 AND t.org = ?14 " +
  "AND t.scope IN ('submit','operator') AND t.revoked = 0 AND t.expires > ?11 " +
  "AND (SELECT count(*) FROM intake WHERE token_hash = t.hash AND day = ?12) " +
  "  + (SELECT count(*) FROM events WHERE token_hash = t.hash AND kind = 'confirm' AND day = ?12) < 20 " +
  "AND (SELECT count(*) FROM intake WHERE state IN ('pending','held','committed')) < 1000 " +
  "ON CONFLICT(lineage_id, request_id) DO NOTHING RETURNING id";

export async function submitLesson(input: SubmitInput, context: DbContext): Promise<SubmitReceipt> {
  requireScope(context, ["submit", "operator"]);
  const params = assertPlainObject(input, ["request_id", "service_id", "kind", "body", "doc_url", "success_evidence", "note_id", "expected_revision"]);
  const requestId = assertUuid(params.request_id);
  const serviceId = assertServiceId(params.service_id);
  if (params.kind !== "procedure" && params.kind !== "lesson") throw invalid();
  const body = assertText(params.body);
  const docUrl = assertDocUrl(params.doc_url);
  const evidence = assertEvidence(params.success_evidence);
  const noteId = params.note_id === undefined ? null : assertNoteId(params.note_id);
  const expectedRevision = params.expected_revision === undefined ? null : assertRevision(params.expected_revision);
  if ((noteId === null) !== (expectedRevision === null)) throw invalid();
  const requestDigest = await sha256Hex(canonicalJson({
    service_id: serviceId, kind: params.kind, body, doc_url: docUrl, success_evidence: evidence,
    note_id: noteId, expected_revision: expectedRevision,
  }));
  const now = unixNow();
  const day = Math.floor(now / 86400);
  const id = crypto.randomUUID();
  const db = context.db;
  const inserted = await returningId(db, SUBMIT_SQL, [
    id, requestId, requestDigest, params.kind, serviceId, noteId, expectedRevision,
    body, docUrl, JSON.stringify(evidence), now, day, context.auth.hash, context.org,
  ]);
  if (inserted) return { intake_id: id, state: "pending", duplicate: false };
  const existing = await db.prepare("SELECT id, request_digest, state FROM intake WHERE lineage_id = ?1 AND request_id = ?2")
    .bind(context.auth.lineage_id, requestId).first<{ id: string; request_digest: string; state: string }>();
  if (existing) {
    if (existing.request_digest !== requestDigest) throw new HubError("IDEMPOTENCY_CONFLICT");
    await requireLiveToken(db, context.auth.hash, context.org, now);
    return { intake_id: existing.id, state: existing.state, duplicate: true };
  }
  await requireLiveToken(db, context.auth.hash, context.org, now);
  const counts = await db.prepare(
    "SELECT (SELECT count(*) FROM intake WHERE token_hash = ?1 AND day = ?2) " +
    "     + (SELECT count(*) FROM events WHERE token_hash = ?1 AND kind = 'confirm' AND day = ?2) AS used",
  ).bind(context.auth.hash, day).first<{ used: number }>();
  if ((counts?.used ?? 0) >= 20) throw new HubError("DAILY_LIMIT");
  const queue = await db.prepare("SELECT count(*) AS n FROM intake WHERE state IN ('pending','held','committed')").first<{ n: number }>();
  if ((queue?.n ?? 0) >= 1000) throw new HubError("QUEUE_FULL");
  throw new HubError("CONFLICT");
}

const CONFIRM_SQL =
  "INSERT INTO events " +
  "(id, intake_id, note_id, revision, lineage_id, token_hash, kind, action, operation_id, value, payload, month, created, day) " +
  "SELECT ?1, NULL, ?2, ?3, t.lineage_id, t.hash, 'confirm', 'confirm', ?4, 0, ?5, NULL, ?6, ?7 " +
  "FROM tokens AS t WHERE t.hash = ?8 AND t.org = ?9 " +
  "AND t.scope IN ('submit','operator') AND t.revoked = 0 AND t.expires > ?6 " +
  "AND (SELECT count(*) FROM intake WHERE token_hash = t.hash AND day = ?7) " +
  "  + (SELECT count(*) FROM events WHERE token_hash = t.hash AND kind = 'confirm' AND day = ?7) < 20 " +
  "ON CONFLICT DO NOTHING RETURNING id";

export async function confirmLesson(input: ConfirmInput, context: DbContext): Promise<ConfirmReceipt> {
  requireScope(context, ["submit", "operator"]);
  const params = assertPlainObject(input, ["note_id", "revision", "success_evidence"]);
  const noteId = assertNoteId(params.note_id);
  const revision = assertRevision(params.revision);
  const evidence = assertEvidence(params.success_evidence);
  const now = unixNow();
  const day = Math.floor(now / 86400);
  const db = context.db;
  const payloadDigest = await sha256Hex(canonicalJson({ kind: "confirm", note_id: noteId, revision, token_hash: context.auth.hash, success_evidence: evidence }));
  const operationId = await sha256Hex(canonicalJson({ kind: "confirm", note_id: noteId, revision, lineage_id: context.auth.lineage_id }));
  const payload = JSON.stringify({ success_evidence: evidence, validation: "pending", payload_digest: payloadDigest, gate_digest: null });
  const inserted = await returningId(db, CONFIRM_SQL, [crypto.randomUUID(), noteId, revision, operationId, payload, now, day, context.auth.hash, context.org]);
  if (inserted) return { note_id: noteId, revision, accepted: true, duplicate: false, validation: "pending" };
  const existing = await currentVote(db, "confirm", noteId, revision, context.auth.lineage_id);
  if (!existing) {
    await requireLiveToken(db, context.auth.hash, context.org, now);
    const counts = await db.prepare(
      "SELECT (SELECT count(*) FROM intake WHERE token_hash = ?1 AND day = ?2) " +
      "     + (SELECT count(*) FROM events WHERE token_hash = ?1 AND kind = 'confirm' AND day = ?2) AS used",
    ).bind(context.auth.hash, day).first<{ used: number }>();
    if ((counts?.used ?? 0) >= 20) throw new HubError("DAILY_LIMIT");
    throw new HubError("CONFLICT");
  }
  // Passed evidence replacement is a no-op for the same live token; a reissued hash needs a new validation.
  const unchanged = (vote: { payload: string; token_hash: string }) => {
    const stored = JSON.parse(vote.payload) as { payload_digest: string; validation: ConfirmReceipt["validation"] };
    return stored.payload_digest === payloadDigest || (stored.validation === "passed" && vote.token_hash === context.auth.hash) ? stored.validation : null;
  };
  const kept = unchanged(existing);
  if (kept) {
    await requireLiveToken(db, context.auth.hash, context.org, now);
    return { note_id: noteId, revision, accepted: true, duplicate: true, validation: kept };
  }
  if (existing.token_hash === context.auth.hash && existing.day >= day) {
    await requireLiveToken(db, context.auth.hash, context.org, now);
    throw new HubError("DAILY_LIMIT");
  }
  // CAS on the exact row read: a concurrent validate_confirm must not be overwritten back to pending.
  const updated = await returningId(db,
    "UPDATE events SET token_hash = ?1, payload = ?2, created = ?3, day = ?4 " +
    "WHERE id = ?5 AND kind = 'confirm' AND payload = ?7 AND token_hash = ?8 AND (token_hash <> ?1 OR day < ?4) " +
    "AND EXISTS (SELECT 1 FROM tokens t WHERE t.hash = ?1 AND t.org = ?6 AND t.scope IN ('submit','operator') AND t.revoked = 0 AND t.expires > ?3) " +
    "AND (SELECT count(*) FROM intake WHERE token_hash = ?1 AND day = ?4) " +
    "  + (SELECT count(*) FROM events WHERE token_hash = ?1 AND kind = 'confirm' AND day = ?4 AND id <> ?5) < 20 RETURNING id",
    [context.auth.hash, payload, now, day, existing.id, context.org, existing.payload, existing.token_hash]);
  if (!updated) {
    await requireLiveToken(db, context.auth.hash, context.org, now);
    const current = await currentVote(db, "confirm", noteId, revision, context.auth.lineage_id);
    if (current && (current.payload !== existing.payload || current.token_hash !== existing.token_hash)) {
      const won = unchanged(current);
      if (won) return { note_id: noteId, revision, accepted: true, duplicate: true, validation: won };
      throw new HubError("CONFLICT");
    }
    if (existing.token_hash === context.auth.hash && existing.day >= day) throw new HubError("DAILY_LIMIT");
    const counts = await db.prepare(
      "SELECT (SELECT count(*) FROM intake WHERE token_hash = ?1 AND day = ?2) " +
      "     + (SELECT count(*) FROM events WHERE token_hash = ?1 AND kind = 'confirm' AND day = ?2 AND id <> ?3) AS used",
    ).bind(context.auth.hash, day, existing.id).first<{ used: number }>();
    if ((counts?.used ?? 0) >= 20) throw new HubError("DAILY_LIMIT");
    throw new HubError("CONFLICT");
  }
  return { note_id: noteId, revision, accepted: true, duplicate: true, validation: "pending" };
}

const REPORT_SQL =
  "INSERT INTO events " +
  "(id, intake_id, note_id, revision, lineage_id, token_hash, kind, action, operation_id, value, payload, month, created, day) " +
  "SELECT ?1, NULL, ?2, ?3, t.lineage_id, t.hash, 'report', 'report', ?4, 0, ?5, NULL, ?6, ?7 " +
  "FROM tokens AS t WHERE t.hash = ?8 AND t.org = ?9 " +
  "AND t.scope IN ('submit','operator') AND t.revoked = 0 AND t.expires > ?6 " +
  "AND (SELECT count(*) FROM events WHERE token_hash = t.hash AND kind = 'report' AND day = ?7) < 10 " +
  "ON CONFLICT DO NOTHING RETURNING id";

export async function reportFailure(input: ReportInput, context: DbContext): Promise<ReportReceipt> {
  requireScope(context, ["submit", "operator"]);
  const params = assertPlainObject(input, ["note_id", "revision", "reason"]);
  const noteId = assertNoteId(params.note_id);
  const revision = assertRevision(params.revision);
  const reason = assertText(params.reason);
  const now = unixNow();
  const day = Math.floor(now / 86400);
  const db = context.db;
  const payloadDigest = await sha256Hex(canonicalJson({ kind: "report", note_id: noteId, revision, token_hash: context.auth.hash, reason }));
  const operationId = await sha256Hex(canonicalJson({ kind: "report", note_id: noteId, revision, lineage_id: context.auth.lineage_id }));
  const payload = JSON.stringify({ reason, payload_digest: payloadDigest });
  const inserted = await returningId(db, REPORT_SQL, [crypto.randomUUID(), noteId, revision, operationId, payload, now, day, context.auth.hash, context.org]);
  if (inserted) return { note_id: noteId, revision, accepted: true, duplicate: false };
  const existing = await currentVote(db, "report", noteId, revision, context.auth.lineage_id);
  if (!existing) {
    await requireLiveToken(db, context.auth.hash, context.org, now);
    const counts = await db.prepare("SELECT count(*) AS used FROM events WHERE token_hash = ?1 AND kind = 'report' AND day = ?2")
      .bind(context.auth.hash, day).first<{ used: number }>();
    if ((counts?.used ?? 0) >= 10) throw new HubError("DAILY_LIMIT");
    throw new HubError("CONFLICT");
  }
  const stored = JSON.parse(existing!.payload) as { payload_digest: string };
  if (stored.payload_digest === payloadDigest) {
    await requireLiveToken(db, context.auth.hash, context.org, now);
    return { note_id: noteId, revision, accepted: true, duplicate: true };
  }
  if (existing.token_hash === context.auth.hash && existing.day >= day) {
    await requireLiveToken(db, context.auth.hash, context.org, now);
    throw new HubError("DAILY_LIMIT");
  }
  const updated = await returningId(db,
    "UPDATE events SET token_hash = ?1, payload = ?2, created = ?3, day = ?4 " +
    "WHERE id = ?5 AND kind = 'report' AND (token_hash <> ?1 OR day < ?4) " +
    "AND EXISTS (SELECT 1 FROM tokens t WHERE t.hash = ?1 AND t.org = ?6 AND t.scope IN ('submit','operator') AND t.revoked = 0 AND t.expires > ?3) " +
    "AND (SELECT count(*) FROM events WHERE token_hash = ?1 AND kind = 'report' AND day = ?4 AND id <> ?5) < 10 RETURNING id",
    [context.auth.hash, payload, now, day, existing!.id, context.org]);
  if (!updated) {
    await requireLiveToken(db, context.auth.hash, context.org, now);
    const current = await currentVote(db, "report", noteId, revision, context.auth.lineage_id);
    if (current?.token_hash === context.auth.hash && current.day >= day) throw new HubError("DAILY_LIMIT");
    const counts = await db.prepare("SELECT count(*) AS used FROM events WHERE token_hash = ?1 AND kind = 'report' AND day = ?2 AND id <> ?3")
      .bind(context.auth.hash, day, existing!.id).first<{ used: number }>();
    if ((counts?.used ?? 0) >= 10) throw new HubError("DAILY_LIMIT");
    throw new HubError("CONFLICT");
  }
  return { note_id: noteId, revision, accepted: true, duplicate: true };
}

async function currentVote(db: D1Database, kind: "confirm" | "report", noteId: string, revision: string, lineageId: string): Promise<{ id: string; payload: string; token_hash: string; day: number } | null> {
  return db.prepare("SELECT id, payload, token_hash, day FROM events WHERE kind = ?1 AND note_id = ?2 AND revision = ?3 AND lineage_id = ?4")
    .bind(kind, noteId, revision, lineageId).first<{ id: string; payload: string; token_hash: string; day: number }>();
}

export async function listIntake(input: { state?: string; after?: string; limit?: number }, context: DbContext): Promise<ListReceipt<IntakeItem>> {
  requireScope(context, ["operator"]);
  const params = assertPlainObject(input, ["state", "after", "limit"]);
  if (params.state !== undefined && INTENT_STATES[params.state as string] !== true) throw invalid();
  const limit = assertLimit(params.limit, 20, 20);
  const cursor = decodeCursor(params.after);
  const rows = await context.db.prepare(
    "SELECT i.id, i.token_hash, i.lineage_id, i.request_id, i.request_digest, i.kind, i.service_id, i.target_note_id, i.expected_revision, i.body, i.doc_url, i.success_evidence, i.state, i.reason_code, i.note_id, i.revision, i.gate_digest, i.commit_sha, i.published_commit, i.created, i.updated, i.day, " +
    "EXISTS(SELECT 1 FROM tokens t WHERE t.hash = i.token_hash AND t.org = ?1 AND t.revoked = 0 AND t.expires > ?2) AS eligible " +
    "FROM intake AS i WHERE EXISTS(SELECT 1 FROM tokens t WHERE t.hash = i.token_hash AND t.org = ?1) " +
    "AND (?3 IS NULL OR i.state = ?3) AND (?4 IS NULL OR i.created > ?4 OR (i.created = ?4 AND i.id > ?5)) " +
    "ORDER BY i.created, i.id LIMIT ?6",
  ).bind(context.org, unixNow(), params.state ?? null, cursor?.created ?? null, cursor?.id ?? null, limit + 1)
    .all<Record<string, unknown>>();
  const page = (rows.results ?? []).slice(0, limit);
  const items = page.map(row => ({
    id: row.id as string, token_hash: row.token_hash as string, lineage_id: row.lineage_id as string,
    request_id: row.request_id as string, request_digest: row.request_digest as string,
    kind: row.kind as string, service_id: row.service_id as string,
    target_note_id: (row.target_note_id as string | null) ?? null, expected_revision: (row.expected_revision as string | null) ?? null,
    body: (row.body as string | null) ?? null, doc_url: (row.doc_url as string | null) ?? null,
    success_evidence: row.success_evidence ? JSON.parse(row.success_evidence as string) as Evidence : null,
    state: row.state as string, reason_code: (row.reason_code as string | null) ?? null,
    note_id: (row.note_id as string | null) ?? null, revision: (row.revision as string | null) ?? null,
    gate_digest: (row.gate_digest as string | null) ?? null, commit_sha: (row.commit_sha as string | null) ?? null,
    published_commit: (row.published_commit as string | null) ?? null,
    created: row.created as number, updated: row.updated as number, day: row.day as number,
    eligible: Boolean(row.eligible),
  }));
  const last = items[items.length - 1];
  return { items, next_cursor: (rows.results ?? []).length > limit && last ? encodeCursor(last.created, last.id) : null };
}

export async function listEvents(input: { note_id?: string; revision?: string; kind?: string; after?: string; limit?: number }, context: DbContext): Promise<ListReceipt<EventItem>> {
  requireScope(context, ["operator"]);
  const params = assertPlainObject(input, ["note_id", "revision", "kind", "after", "limit"]);
  if (params.kind !== undefined && (typeof params.kind !== "string" || !/^[a-z_]{1,32}$/.test(params.kind))) throw invalid();
  const kind = (params.kind as string | undefined) ?? null;
  const hasNote = params.note_id !== undefined;
  if (hasNote !== (params.revision !== undefined)) throw invalid();
  const noteId = hasNote ? assertNoteId(params.note_id) : null;
  const revision = hasNote ? assertRevision(params.revision) : null;
  const limit = assertLimit(params.limit, 100, 100);
  const cursor = decodeCursor(params.after);
  const rows = await context.db.prepare(
    "SELECT e.id, e.intake_id, e.note_id, e.revision, e.lineage_id, e.token_hash, e.kind, e.action, e.operation_id, e.value, e.payload, e.month, e.created, e.day, " +
    "EXISTS(SELECT 1 FROM tokens t WHERE t.hash = e.token_hash AND t.org = ?1 AND t.revoked = 0 AND t.expires > ?2) AS eligible " +
    "FROM events AS e WHERE EXISTS(SELECT 1 FROM tokens t WHERE t.hash = e.token_hash AND t.org = ?1) " +
    "AND (?3 IS NULL OR (e.note_id = ?3 AND e.revision = ?4)) " +
    "AND (?5 IS NULL OR e.created > ?5 OR (e.created = ?5 AND e.id > ?6)) " +
    "AND (?8 IS NULL OR e.kind = ?8) " +
    "ORDER BY e.created, e.id LIMIT ?7",
  ).bind(context.org, unixNow(), noteId, revision, cursor?.created ?? null, cursor?.id ?? null, limit + 1, kind)
    .all<Record<string, unknown>>();
  const page = (rows.results ?? []).slice(0, limit);
  const items = page.map(row => ({
    id: row.id as string, intake_id: (row.intake_id as string | null) ?? null,
    note_id: (row.note_id as string | null) ?? null, revision: (row.revision as string | null) ?? null,
    lineage_id: row.lineage_id as string, token_hash: row.token_hash as string,
    kind: row.kind as string, action: row.action as string, operation_id: row.operation_id as string,
    value: row.value as number, payload: JSON.parse((row.payload as string) ?? "{}") as Record<string, unknown>,
    month: (row.month as string | null) ?? null, created: row.created as number, day: row.day as number,
    eligible: Boolean(row.eligible),
  }));
  const last = items[items.length - 1];
  return { items, next_cursor: (rows.results ?? []).length > limit && last ? encodeCursor(last.created, last.id) : null };
}

/** Conditional transition; the row's org and the operator's live scope are checked in the write. */
async function casTransition(context: DbContext, intakeId: string, state: string, sql: string, params: unknown[], same: (row: Record<string, unknown>) => boolean): Promise<TransitionReceipt> {
  const guard = " AND EXISTS (SELECT 1 FROM tokens owner WHERE owner.hash = intake.token_hash AND owner.org = ?)" +
    " AND EXISTS (SELECT 1 FROM tokens actor WHERE actor.hash = ? AND actor.org = ? AND actor.scope = 'operator' AND actor.revoked = 0 AND actor.expires > ?) RETURNING id";
  if (await returningId(context.db, sql + guard, [...params, context.org, context.auth.hash, context.org, unixNow()])) return { intake_id: intakeId, state, duplicate: false };
  const live = await context.db.prepare("SELECT 1 FROM tokens WHERE hash = ? AND org = ? AND scope = 'operator' AND revoked = 0 AND expires > ?")
    .bind(context.auth.hash, context.org, unixNow()).first();
  if (!live) throw new HubError("UNAUTHORIZED");
  const row = await context.db.prepare("SELECT i.* FROM intake i JOIN tokens owner ON owner.hash = i.token_hash AND owner.org = ? WHERE i.id = ?")
    .bind(context.org, intakeId).first<Record<string, unknown>>();
  if (!row) throw new HubError("NOT_FOUND");
  if (same(row)) return { intake_id: intakeId, state, duplicate: true };
  throw new HubError("CONFLICT");
}

export type TransitionReceipt =
  | { intake_id: string; state: string; duplicate: boolean }
  | { event_id: string; action?: string; note_id?: string; revision?: string; validation?: string; duplicate: boolean };

export async function transitionIntake(input: TransitionInput, context: DbContext): Promise<TransitionReceipt> {
  requireScope(context, ["operator"]);
  if (input.action !== "hold" && input.action !== "reject" && input.action !== "committed" && input.action !== "ack" && input.action !== "review" && input.action !== "validate_confirm" && input.action !== "redact_report") throw invalid();
  const db = context.db;
  const now = unixNow();
  const actor = await db.prepare("SELECT 1 FROM tokens WHERE hash = ? AND org = ? AND scope = 'operator' AND revoked = 0 AND expires > ?")
    .bind(context.auth.hash, context.org, now).first();
  if (!actor) throw new HubError("UNAUTHORIZED");
  if (input.action === "hold" || input.action === "reject") {
    const params = assertPlainObject(input, ["action", "intake_id", "reason_code", "gate_digest"]);
    const intakeId = assertUuid(params.intake_id);
    const reasonCode = assertReasonCode(params.reason_code);
    const gateDigest = params.gate_digest === undefined ? null : assertGateDigest(params.gate_digest);
    const row = await db.prepare("SELECT i.state, i.reason_code FROM intake i JOIN tokens owner ON owner.hash = i.token_hash AND owner.org = ? WHERE i.id = ?")
      .bind(context.org, intakeId).first<{ state: string; reason_code: string | null }>();
    if (!row) throw new HubError("NOT_FOUND");
    if (input.action === "hold") {
      if (row.state === "held" && row.reason_code === reasonCode) return { intake_id: intakeId, state: "held", duplicate: true };
      if (row.state !== "pending" && row.state !== "held") throw new HubError("CONFLICT");
      return casTransition(context, intakeId, "held",
        "UPDATE intake SET state = 'held', reason_code = ?1, gate_digest = COALESCE(?2, gate_digest), updated = ?3 WHERE id = ?4 AND state IN ('pending','held')",
        [reasonCode, gateDigest, now, intakeId], current => current.state === "held" && current.reason_code === reasonCode);
    }
    if (row.state === "rejected") {
      if (row.reason_code === reasonCode) return { intake_id: intakeId, state: "rejected", duplicate: true };
      throw new HubError("CONFLICT");
    }
    if (row.state !== "pending" && row.state !== "held" && row.state !== "committed") throw new HubError("CONFLICT");
    if (row.state === "committed" && TERMINAL_REASONS[reasonCode] !== true) throw invalid();
    // A non-terminal reason may only end a still-open (pending/held) intake; the SQL re-asserts that under races.
    return casTransition(context, intakeId, "rejected",
      "UPDATE intake SET state = 'rejected', reason_code = ?1, gate_digest = COALESCE(?2, gate_digest), body = NULL, doc_url = NULL, success_evidence = NULL, updated = ?3 " +
      "WHERE id = ?4 AND (state IN ('pending','held') OR (state = 'committed' AND ?5 = 1))",
      [reasonCode, gateDigest, now, intakeId, TERMINAL_REASONS[reasonCode] === true ? 1 : 0],
      current => current.state === "rejected" && current.reason_code === reasonCode);
  }
  if (input.action === "committed") {
    const params = assertPlainObject(input, ["action", "intake_id", "note_id", "revision", "gate_digest", "commit"]);
    const intakeId = assertUuid(params.intake_id);
    const noteId = assertNoteId(params.note_id);
    const revision = assertRevision(params.revision);
    const gateDigest = assertGateDigest(params.gate_digest);
    const commit = assertCommit(params.commit);
    const row = await db.prepare("SELECT i.state, i.note_id, i.revision, i.gate_digest, i.commit_sha FROM intake i JOIN tokens owner ON owner.hash = i.token_hash AND owner.org = ? WHERE i.id = ?")
      .bind(context.org, intakeId).first<{ state: string; note_id: string | null; revision: string | null; gate_digest: string | null; commit_sha: string | null }>();
    if (!row) throw new HubError("NOT_FOUND");
    if (row.state === "committed" && row.note_id === noteId && row.revision === revision && row.gate_digest === gateDigest && row.commit_sha === commit) {
      return { intake_id: intakeId, state: "committed", duplicate: true };
    }
    if (row.state !== "pending" && row.state !== "held") throw new HubError("CONFLICT");
    return casTransition(context, intakeId, "committed",
      "UPDATE intake SET state = 'committed', note_id = ?1, revision = ?2, gate_digest = ?3, commit_sha = ?4, updated = ?5 WHERE id = ?6 AND state IN ('pending','held')",
      [noteId, revision, gateDigest, commit, now, intakeId],
      current => current.state === "committed" && current.note_id === noteId && current.revision === revision && current.gate_digest === gateDigest && current.commit_sha === commit);
  }
  if (input.action === "ack") {
    // §A deletion list removes Worker-side note-index re-verification on ack: this is a receipt transition only.
    const params = assertPlainObject(input, ["action", "intake_id", "revision", "commit", "release_commit"]);
    const intakeId = assertUuid(params.intake_id);
    const revision = assertRevision(params.revision);
    const commit = assertCommit(params.commit);
    const releaseCommit = assertCommit(params.release_commit);
    const row = await db.prepare("SELECT i.state, i.revision, i.commit_sha, i.published_commit FROM intake i JOIN tokens owner ON owner.hash = i.token_hash AND owner.org = ? WHERE i.id = ?")
      .bind(context.org, intakeId).first<{ state: string; revision: string | null; commit_sha: string | null; published_commit: string | null }>();
    if (!row) throw new HubError("NOT_FOUND");
    if (row.commit_sha !== commit) throw new HubError("CONFLICT");
    if (row.state === "published" && row.revision === revision && row.published_commit === releaseCommit) {
      return { intake_id: intakeId, state: "published", duplicate: true };
    }
    if (row.state !== "committed" || row.revision !== revision) throw new HubError("CONFLICT");
    return casTransition(context, intakeId, "published",
      "UPDATE intake SET state = 'published', published_commit = ?1, body = NULL, doc_url = NULL, success_evidence = NULL, updated = ?3 WHERE id = ?4 AND state = 'committed' AND revision = ?5 AND commit_sha = ?2",
      [releaseCommit, commit, now, intakeId, revision],
      current => current.state === "published" && current.revision === revision && current.published_commit === releaseCommit);
  }
  if (input.action === "review") {
    const params = assertPlainObject(input, ["action", "note_id", "revision", "decision", "gate_digest", "events_digest", "request_id"]);
    const noteId = assertNoteId(params.note_id);
    const revision = assertRevision(params.revision);
    if (params.decision !== "promote" && params.decision !== "recall") throw invalid();
    const gateDigest = assertGateDigest(params.gate_digest);
    // H3 canonical_digest form (sha256:<hex>), matched verbatim against payload.events_digest by H3's aggregation.
    const eventsDigest = assertRevision(params.events_digest);
    const requestId = assertUuid(params.request_id);
    const action = params.decision === "promote" ? "review_promote" : "review_recall";
    const payloadDigest = await sha256Hex(canonicalJson({ kind: "review", note_id: noteId, revision, token_hash: context.auth.hash, decision: params.decision, gate_digest: gateDigest, events_digest: eventsDigest }));
    // H3 _find_review reads revision from the payload, not the event column.
    const payload = JSON.stringify({ revision, decision: params.decision, gate_digest: gateDigest, events_digest: eventsDigest, payload_digest: payloadDigest });
    const inserted = await returningId(db,
      "INSERT INTO events (id, intake_id, note_id, revision, lineage_id, token_hash, kind, action, operation_id, value, payload, month, created, day) " +
      "SELECT ?1, NULL, ?2, ?3, actor.lineage_id, actor.hash, 'ack', ?4, ?5, 0, ?6, NULL, ?7, ?8 FROM tokens actor " +
      "WHERE actor.hash = ?9 AND actor.org = ?10 AND actor.scope = 'operator' AND actor.revoked = 0 AND actor.expires > ?7 ON CONFLICT DO NOTHING RETURNING id",
      [crypto.randomUUID(), noteId, revision, action, requestId, payload, now, Math.floor(now / 86400), context.auth.hash, context.org]);
    if (inserted) return { event_id: inserted, action, note_id: noteId, revision, duplicate: false };
    const existing = await db.prepare("SELECT id, payload FROM events WHERE kind = 'ack' AND operation_id = ?1").bind(requestId).first<{ id: string; payload: string }>();
    if (!existing) throw new HubError("CONFLICT");
    const stored = JSON.parse(existing.payload) as { payload_digest: string };
    if (stored.payload_digest !== payloadDigest) throw new HubError("IDEMPOTENCY_CONFLICT");
    return { event_id: existing.id, action, note_id: noteId, revision, duplicate: true };
  }
  if (input.action === "redact_report") {
    const params = assertPlainObject(input, ["action", "event_id", "expected_payload_digest"]);
    const eventId = assertUuid(params.event_id);
    const expectedDigest = assertDigest(params.expected_payload_digest);
    const updated = await returningId(db,
      "UPDATE events SET payload = json_remove(payload, '$.reason') WHERE id = ?1 AND kind = 'report' " +
      "AND json_extract(payload, '$.payload_digest') = ?2 AND json_type(payload, '$.reason') IS NOT NULL " +
      "AND EXISTS (SELECT 1 FROM tokens owner WHERE owner.hash = events.token_hash AND owner.org = ?3) " +
      "AND EXISTS (SELECT 1 FROM tokens actor WHERE actor.hash = ?4 AND actor.org = ?3 AND actor.scope = 'operator' AND actor.revoked = 0 AND actor.expires > ?5) RETURNING id",
      [eventId, expectedDigest, context.org, context.auth.hash, now]);
    if (updated) return { event_id: eventId, action: "redact_report", duplicate: false };
    const row = await db.prepare("SELECT e.payload FROM events e JOIN tokens owner ON owner.hash = e.token_hash AND owner.org = ?1 WHERE e.id = ?2 AND e.kind = 'report'")
      .bind(context.org, eventId).first<{ payload: string }>();
    if (!row) throw new HubError("NOT_FOUND");
    const stored = JSON.parse(row.payload) as { payload_digest: string; reason?: string };
    if (stored.payload_digest === expectedDigest && !Object.hasOwn(stored, "reason")) {
      return { event_id: eventId, action: "redact_report", duplicate: true };
    }
    throw new HubError("CONFLICT");
  }
  const params = assertPlainObject(input, ["action", "event_id", "expected_payload_digest", "gate_digest", "validation"]);
  const eventId = assertUuid(params.event_id);
  const expectedDigest = assertDigest(params.expected_payload_digest);
  const gateDigest = assertGateDigest(params.gate_digest);
  if (params.validation !== "passed" && params.validation !== "held" && params.validation !== "rejected") throw invalid();
  const updated = await returningId(db,
    "UPDATE events SET payload = CASE WHEN ?1 = 'rejected' THEN json_set(json_remove(payload, '$.success_evidence'), '$.validation', ?1, '$.gate_digest', ?2) ELSE json_set(payload, '$.validation', ?1, '$.gate_digest', ?2) END " +
    "WHERE id = ?3 AND kind = 'confirm' AND json_extract(payload, '$.payload_digest') = ?4 AND json_extract(payload, '$.validation') = 'pending' " +
    "AND EXISTS (SELECT 1 FROM tokens owner WHERE owner.hash = events.token_hash AND owner.org = ?5) " +
    "AND EXISTS (SELECT 1 FROM tokens actor WHERE actor.hash = ?6 AND actor.org = ?5 AND actor.scope = 'operator' AND actor.revoked = 0 AND actor.expires > ?7) RETURNING id",
    [params.validation, gateDigest, eventId, expectedDigest, context.org, context.auth.hash, now]);
  if (updated) return { event_id: eventId, validation: params.validation, duplicate: false };
  const row = await db.prepare("SELECT payload FROM events WHERE id = ?1 AND kind = 'confirm'").bind(eventId).first<{ payload: string }>();
  if (!row) throw new HubError("NOT_FOUND");
  const stored = JSON.parse(row.payload) as { payload_digest: string; validation: string };
  if (stored.payload_digest === expectedDigest && stored.validation === params.validation) {
    return { event_id: eventId, validation: params.validation, duplicate: true };
  }
  throw new HubError("CONFLICT");
}
