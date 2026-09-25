export type Scope = "read" | "submit" | "operator";

export type AuthContext = { hash: string; org: string; scope: Scope; lineage_id: string; expires: number };

/** Minimal bindings auth needs. index.ts composes the full HubEnv on top of this. */
export type AuthEnv = { HUB_DB: D1Database; HUB_ORG: string };

/** Execution context shared by the DB-backed operations. */
export type DbContext = { db: D1Database; auth: AuthContext; org: string };

const CODES = {
  UNAUTHORIZED: 401,
  FORBIDDEN: 403,
  INVALID_PARAMS: 400,
  SECRET_REJECTED: 400,
  PAYLOAD_TOO_LARGE: 413,
  NOT_FOUND: 404,
  RESERVATION_NOT_FOUND: 404,
  CONFLICT: 409,
  IDEMPOTENCY_CONFLICT: 409,
  BUDGET_EXHAUSTED: 409,
  SETTLEMENT_CONFLICT: 409,
  SETTLEMENT_EXCEEDS_RESERVATION: 409,
  DAILY_LIMIT: 429,
  QUEUE_FULL: 503,
  CONFIG_INVALID: 500,
} as const;

export type ErrorCode = keyof typeof CODES;

/** Fixed-code error. The message is the code, never a payload description. */
export class HubError extends Error {
  readonly code: ErrorCode;
  readonly status: number;
  constructor(code: ErrorCode) {
    super(code);
    this.name = "HubError";
    this.code = code;
    this.status = CODES[code];
  }
}

export const TOKEN_RE = /^[A-Za-z0-9_-]{43}$/;
/** Authentication honors a registered legacy bearer without assuming its issued format. New issuance stays TOKEN_RE. */
const BEARER_RE = /^Bearer ([A-Za-z0-9._~+\/=-]{1,1024})$/i;
export const HASH_RE = /^[0-9a-f]{64}$/;

export function sha256Hex(input: string | Uint8Array): Promise<string> {
  const bytes = typeof input === "string" ? new TextEncoder().encode(input) : input;
  return crypto.subtle.digest("SHA-256", new Uint8Array(bytes)).then(digest =>
    [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join(""));
}

/** Canonical JSON: sorted object keys, no whitespace, JSON string escaping preserved. */
export function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "number" || typeof value === "string") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map(key => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(",")}}`;
}

function base64url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** 32 random bytes, base64url, 43 characters. */
export function createToken(): string {
  return base64url(crypto.getRandomValues(new Uint8Array(32)));
}

export async function hashToken(token: string): Promise<string> {
  return sha256Hex(token);
}

function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

/**
 * Resolves the caller's bearer credential against this Hub's own D1 only.
 * No cache: expires, org, revoked are re-checked on every request.
 */
export async function authenticate(request: Request, env: AuthEnv): Promise<AuthContext> {
  const header = request.headers.get("authorization") ?? "";
  const match = BEARER_RE.exec(header);
  if (!match) throw new HubError("UNAUTHORIZED");
  const hash = await hashToken(match[1]);
  const now = nowSeconds();
  const row = await env.HUB_DB
    .prepare("SELECT hash, org, scope, lineage_id, expires FROM tokens WHERE hash = ?1 AND revoked = 0 AND expires > ?2")
    .bind(hash, now)
    .first<{ hash: string; org: string; scope: string; lineage_id: string; expires: number }>();
  if (!row || row.org !== env.HUB_ORG) throw new HubError("UNAUTHORIZED");
  return { hash: row.hash, org: row.org, scope: row.scope as Scope, lineage_id: row.lineage_id, expires: row.expires };
}

export type ManageTokenInput =
  | { action: "issue"; scope: Scope; expires: number }
  | { action: "reissue"; token_hash: string; expires: number }
  | { action: "revoke"; token_hash: string };

export type TokenReceipt =
  | { action: "issue"; token: string; hash: string; org: string; scope: Scope; expires: number; lineage_id: string }
  | { action: "reissue"; token: string; hash: string; org: string; scope: Scope; expires: number; lineage_id: string }
  | { action: "revoke"; token_hash: string; revoked: true; duplicate: boolean };

/** Rejects non-objects and any field outside `allowed` (this Hub accepts no extra fields). */
export function assertPlainObject(value: unknown, allowed: string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new HubError("INVALID_PARAMS");
  const keys = Object.keys(value);
  if (keys.some(key => !allowed.includes(key))) throw new HubError("INVALID_PARAMS");
  return value as Record<string, unknown>;
}

/** Operator/scope gate. The org must match the authenticated token's org. */
export function requireScope(context: DbContext, allowed: Scope[]): void {
  if (context.auth.org !== context.org || !allowed.includes(context.auth.scope)) throw new HubError("FORBIDDEN");
}

function assertExpiry(value: unknown): number {
  if (!Number.isInteger(value) || (value as number) <= nowSeconds()) throw new HubError("INVALID_PARAMS");
  return value as number;
}

function assertTokenHash(value: unknown): string {
  if (typeof value !== "string" || !HASH_RE.test(value)) throw new HubError("INVALID_PARAMS");
  return value;
}

/** SQL predicate over alias `c`: the caller (?h) is still a live operator of org ?o at time ?n. */
const LIVE_CALLER = (h: number, o: number, n: number) =>
  `c.hash = ?${h} AND c.org = ?${o} AND c.scope = 'operator' AND c.revoked = 0 AND c.expires > ?${n}`;

/** Classifies a write that matched nothing: a caller revoked/expired/demoted since authentication is UNAUTHORIZED. */
async function requireLiveCaller(context: DbContext, now: number): Promise<void> {
  const row = await context.db.prepare(`SELECT c.hash FROM tokens AS c WHERE ${LIVE_CALLER(1, 2, 3)}`)
    .bind(context.auth.hash, context.org, now).first<{ hash: string }>();
  if (!row) throw new HubError("UNAUTHORIZED");
}

/**
 * issue | reissue | revoke. The plaintext token is returned exactly once.
 * Every write re-checks the caller inside the same statement: auth ran before the body was read.
 */
export async function manageToken(input: ManageTokenInput, context: DbContext): Promise<TokenReceipt> {
  requireScope(context, ["operator"]);
  const db = context.db;
  if (input.action === "issue") {
    const params = assertPlainObject(input, ["action", "scope", "expires"]);
    if (params.scope !== "read" && params.scope !== "submit" && params.scope !== "operator") throw new HubError("INVALID_PARAMS");
    const expires = assertExpiry(params.expires);
    const token = createToken();
    const hash = await hashToken(token);
    const lineage_id = crypto.randomUUID();
    const created = nowSeconds();
    const result = await db.prepare(
      "INSERT INTO tokens (hash, org, scope, expires, lineage_id, revoked, created) " +
      `SELECT ?1, ?2, ?3, ?4, ?5, 0, ?6 FROM tokens AS c WHERE ${LIVE_CALLER(7, 2, 6)}`,
    ).bind(hash, context.org, params.scope, expires, lineage_id, created, context.auth.hash).run();
    if (Number(result.meta?.changes ?? 0) !== 1) throw new HubError("UNAUTHORIZED");
    return { action: "issue", token, hash, org: context.org, scope: params.scope as Scope, expires, lineage_id };
  }
  if (input.action === "reissue") {
    const params = assertPlainObject(input, ["action", "token_hash", "expires"]);
    const oldHash = assertTokenHash(params.token_hash);
    const expires = assertExpiry(params.expires);
    const now = nowSeconds();
    const token = createToken();
    const hash = await hashToken(token);
    // Same batch: revoke the live token (only while the caller is a live operator), then insert only when that UPDATE changed exactly one row.
    await db.batch([
      db.prepare(
        "UPDATE tokens SET revoked = 1 WHERE hash = ?1 AND org = ?2 AND revoked = 0 AND expires > ?3 " +
        `AND EXISTS (SELECT 1 FROM tokens AS c WHERE ${LIVE_CALLER(4, 2, 3)})`,
      ).bind(oldHash, context.org, now, context.auth.hash),
      db.prepare(
        "INSERT INTO tokens (hash, org, scope, expires, lineage_id, revoked, created) " +
        "SELECT ?1, t.org, t.scope, ?2, t.lineage_id, 0, ?3 FROM tokens AS t " +
        "WHERE t.hash = ?4 AND t.org = ?5 AND t.revoked = 1 AND changes() = 1",
      ).bind(hash, expires, now, oldHash, context.org),
    ]);
    const row = await db.prepare("SELECT hash, org, scope, lineage_id, expires FROM tokens WHERE hash = ?1")
      .bind(hash).first<{ hash: string; org: string; scope: string; lineage_id: string; expires: number }>();
    if (!row) {
      await requireLiveCaller(context, now);
      throw new HubError("CONFLICT");
    }
    return { action: "reissue", token, hash: row.hash, org: row.org, scope: row.scope as Scope, expires: row.expires, lineage_id: row.lineage_id };
  }
  const params = assertPlainObject(input, ["action", "token_hash"]);
  if (params.action !== "revoke") throw new HubError("INVALID_PARAMS");
  const tokenHash = assertTokenHash(params.token_hash);
  const now = nowSeconds();
  const result = await db.prepare(
    "UPDATE tokens SET revoked = 1 WHERE hash = ?1 AND org = ?2 AND revoked = 0 " +
    `AND EXISTS (SELECT 1 FROM tokens AS c WHERE ${LIVE_CALLER(3, 2, 4)})`,
  ).bind(tokenHash, context.org, context.auth.hash, now).run();
  const changed = Number(result.meta?.changes ?? 0);
  if (changed === 0) {
    await requireLiveCaller(context, now);
    const row = await db.prepare("SELECT hash FROM tokens WHERE hash = ?1 AND org = ?2 AND revoked = 1").bind(tokenHash, context.org).first<{ hash: string }>();
    if (!row) throw new HubError("NOT_FOUND");
    return { action: "revoke", token_hash: tokenHash, revoked: true, duplicate: true };
  }
  return { action: "revoke", token_hash: tokenHash, revoked: true, duplicate: false };
}
