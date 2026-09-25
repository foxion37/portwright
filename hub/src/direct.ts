import patterns from '../../install/secret-patterns.json' with { type: 'json' };
import intakeSchema from '../contracts/intake.schema.json' with { type: 'json' };
import { jsonResponse, readBody, BodyTooLargeError, corsHeaders } from './http.ts';
import { ReleaseError, sha256 } from './r2-objects.ts';
import { canonicalUri, mimeType, readDirectory, readNoteResource, readResource, resolveAlias, visibleSnapshot } from './release-reader.ts';
import type { NoteEntry } from './release-reader.ts';
import { readRecalls } from './recalls.ts';
import type { AuthContext } from './auth.ts';
import type { HubEnv } from './index.ts';
import { submitLesson, confirmLesson, reportFailure } from './intake.ts';
import type { SubmitInput, ConfirmInput, ReportInput } from './intake.ts';

type Rpc = { jsonrpc: '2.0'; id?: string | number | null; method: string; params?: Record<string, unknown> };
const rules = patterns.patterns.map(({ regex, flags }) => new RegExp(regex, flags ?? ''));
const normalization = 'normalization' in patterns ? patterns.normalization : ['raw', 'percent-decode-once'];
if (!Array.isArray(normalization) || normalization.length !== 2 || !normalization.includes('raw') || !normalization.includes('percent-decode-once')) throw new Error('Invalid secret normalization policy');
const percentDecoder = new TextDecoder();
function hasSecret(text: string): boolean {
  if (rules.some(rule => rule.test(text))) return true;
  if (!text.includes('%')) return false;
  const decoded = text.replace(/(?:%[0-9a-fA-F]{2})+/g, sequence => {
    try { return decodeURIComponent(sequence); }
    catch {
      const bytes = new Uint8Array(sequence.length / 3);
      for (let i = 0; i < bytes.length; i++) bytes[i] = parseInt(sequence.slice(i * 3 + 1, i * 3 + 3), 16);
      return percentDecoder.decode(bytes);
    }
  });
  return rules.some(rule => rule.test(decoded));
}
const object = (x: unknown): x is Record<string, unknown> => !!x && typeof x === 'object' && !Array.isArray(x);
const MODERN = '2026-07-28';
const VERSIONS = ['2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05', '2024-10-07'];

export class InputError extends Error {
  constructor(readonly code: string, readonly status = 400) { super(code); }
}

export async function safeJson(request: Request, limit = 16 * 1024, maxLeaves = 64): Promise<Record<string, unknown>> {
  let bytes: ArrayBuffer;
  try { bytes = await readBody(request, limit); }
  catch (error) { if (error instanceof BodyTooLargeError) throw new InputError('PAYLOAD_TOO_LARGE', 413); throw new InputError('INVALID_PARAMS'); }
  let text: string;
  try { text = new TextDecoder('utf-8', { fatal: true }).decode(bytes); }
  catch { throw new InputError('INVALID_PARAMS'); }
  if (hasSecret(text)) throw new InputError('SECRET_REJECTED');
  let parsed: unknown;
  try { parsed = JSON.parse(text); } catch { throw new InputError('INVALID_PARAMS'); }
  let leaves = 0;
  function check(value: unknown, depth: number): void {
    if (depth > 8 || ++leaves > maxLeaves) throw new InputError('INVALID_PARAMS');
    if (typeof value === 'string') {
      if (value.includes('\0')) throw new InputError('INVALID_PARAMS');
      if (hasSecret(value)) throw new InputError('SECRET_REJECTED');
    } else if (Array.isArray(value)) for (const item of value) check(item, depth + 1);
    else if (object(value)) for (const [key, item] of Object.entries(value)) { check(key, depth + 1); check(item, depth + 1); }
  }
  check(parsed, 0);
  if (!object(parsed)) throw new InputError('INVALID_PARAMS');
  return parsed;
}

function rpcError(request: Request, id: Rpc['id'], code: number, label: string, status = 200, data?: unknown): Response {
  return jsonResponse(request, { jsonrpc: '2.0', id: id ?? null, error: { code, message: label, ...(data === undefined ? {} : { data }) } }, status);
}
function decodeHeader(value: string | null): string | null {
  if (!value?.startsWith('=?base64?')) return value;
  const match = /^=\?base64\?([A-Za-z0-9+/]*={0,2})\?=$/.exec(value);
  try { return match && btoa(atob(match[1])) === match[1] ? new TextDecoder('utf-8', { fatal: true }).decode(Uint8Array.from(atob(match[1]), c => c.charCodeAt(0))) : null; }
  catch { return null; }
}
function schema(name: string): Record<string, unknown> {
  const defs = intakeSchema.$defs as Record<string, Record<string, unknown>>;
  function expand(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(expand);
    if (!object(value)) return value;
    if (typeof value.$ref === 'string') return expand(defs[value.$ref.slice('#/$defs/'.length)]);
    return Object.fromEntries(Object.entries(value).map(([key, part]) => [key, expand(part)]));
  }
  return expand(defs[name]) as Record<string, unknown>;
}
const toolNames = ['submit_lesson', 'confirm_lesson', 'report_failure'] as const;
const tools = toolNames.map(name => ({ name, description: name.replace('_', ' '), inputSchema: schema(name) }));
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (object(value)) return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}`;
  return JSON.stringify(value);
}

export async function directFetch(request: Request, env: HubEnv, auth: AuthContext): Promise<Response> {
  const url = new URL(request.url);
  if (url.pathname === '/sync') {
    if (request.method !== 'GET') return jsonResponse(request, { code: 'METHOD_NOT_ALLOWED' }, 405);
    if (env.HUB_AUDIENCE === 'personal') return jsonResponse(request, { code: 'NOT_FOUND' }, 404);
    const trial = url.searchParams.get('include_trial');
    if ([...url.searchParams.keys()].some(k => k !== 'include_trial') || (trial !== null && trial !== 'true' && trial !== 'false')) return jsonResponse(request, { code: 'INVALID_PARAMS' }, 400);
    try {
      const snapshot = await visibleSnapshot(env.SKILLS_BUCKET, { audience: env.HUB_AUDIENCE, includeTrial: trial === 'true' });
      if (snapshot.visibleNotes.length > 1000) throw new InputError('PAYLOAD_TOO_LARGE', 413);
      const notes: Array<NoteEntry & { text: string }> = [];
      let size = 0;
      for (const note of [...snapshot.visibleNotes].sort((a,b) => a.note_id.localeCompare(b.note_id) || a.revision.localeCompare(b.revision))) {
        if (note.size > 1024 * 1024) throw new InputError('PAYLOAD_TOO_LARGE', 413);
        const data = await readNoteResource(env.SKILLS_BUCKET, snapshot, note.note_id, note.revision);
        size += note.size;
        if (size > 32 * 1024 * 1024) throw new InputError('PAYLOAD_TOO_LARGE', 413);
        notes.push({ ...note, text: data.text });
      }
      const recalls = await readRecalls(env.SKILLS_BUCKET, env.HUB_AUDIENCE);
      if (recalls.entries.some(entry => notes.some(note => note.note_id === entry.note_id && note.revision === entry.revision))) throw new ReleaseError('Recall projection changed', 503);
      const { etag: _etag, ...projection } = recalls;
      return jsonResponse(request, { schema_version: 1, audience: env.HUB_AUDIENCE, release_identity: snapshot.identity, notes,
        notes_digest: await sha256(canonical(notes)), recalls: projection, recalls_digest: await sha256(canonical(projection)) });
    } catch (error) { return jsonResponse(request, { code: error instanceof InputError ? error.code : error instanceof ReleaseError && error.status === 404 ? 'NOT_FOUND' : 'DELIVERY_UNAVAILABLE' }, error instanceof InputError ? error.status : error instanceof ReleaseError && error.status === 404 ? 404 : 503); }
  }
  if (url.pathname !== '/mcp') return jsonResponse(request, { code: 'NOT_FOUND' }, 404);
  const origin = request.headers.get('origin');
  if (origin && origin !== url.origin) return jsonResponse(request, { code: 'INVALID_ORIGIN' }, 403);
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: corsHeaders(request) });
  if (request.method !== 'POST') return jsonResponse(request, { code: 'METHOD_NOT_ALLOWED' }, 405);
  if (!/^application\/json(?:\s*;|$)/i.test(request.headers.get('content-type') ?? '')) return jsonResponse(request, { code: 'INVALID_CONTENT_TYPE' }, 415);
  let rpc: Rpc;
  try { rpc = await safeJson(request) as Rpc; }
  catch (error) { return rpcError(request, null, -32700, error instanceof InputError ? error.code : 'INVALID_PARAMS', error instanceof InputError ? error.status : 400); }
  if (!object(rpc) || rpc.jsonrpc !== '2.0' || typeof rpc.method !== 'string' || (rpc.id !== undefined && rpc.id !== null && typeof rpc.id !== 'string' && typeof rpc.id !== 'number') || (rpc.params !== undefined && !object(rpc.params))) return rpcError(request, null, -32600, 'INVALID_PARAMS', 400);
  const params = rpc.params ?? {};
  const meta = object(params._meta) ? params._meta : {};
  const protocol = request.headers.get('mcp-protocol-version');
  const modern = rpc.method === 'server/discover' || 'io.modelcontextprotocol/protocolVersion' in meta || !!(protocol && protocol >= MODERN);
  if (modern) {
    const accept = request.headers.get('accept') ?? '';
    if (!accept.includes('application/json') || !accept.includes('text/event-stream')) return jsonResponse(request, { code: 'INVALID_ACCEPT' }, 406);
    if (typeof meta['io.modelcontextprotocol/protocolVersion'] !== 'string' || !object(meta['io.modelcontextprotocol/clientCapabilities'])) return rpcError(request, rpc.id, -32602, 'INVALID_PARAMS', 400);
    const named = ['tools/call', 'resources/read', 'prompts/get'].includes(rpc.method);
    if (protocol !== meta['io.modelcontextprotocol/protocolVersion'] || request.headers.get('mcp-method') !== rpc.method || (named && decodeHeader(request.headers.get('mcp-name')) !== (params.name ?? params.uri))) return rpcError(request, rpc.id, -32020, 'INVALID_PARAMS', 400);
    if (protocol !== MODERN) return rpcError(request, rpc.id, -32022, 'UNSUPPORTED_VERSION', 400, { supported: [MODERN] });
  } else if (protocol && !VERSIONS.includes(protocol)) return rpcError(request, rpc.id, -32600, 'UNSUPPORTED_VERSION', 400);
  if (rpc.id === undefined) return jsonResponse(request, undefined, 202);
  const info = { name: 'portwright', version: '0.2.0' };
  const capabilities = { tools: { listChanged: false }, resources: { listChanged: false }, extensions: { 'io.modelcontextprotocol/skills': { directoryRead: true } } };
  const respond = (result: Record<string, unknown>) => jsonResponse(request, { jsonrpc: '2.0', id: rpc.id, result: modern ? { ...result, resultType: 'complete', ttlMs: 0, cacheScope: 'private', _meta: { ...(object(result._meta) ? result._meta : {}), 'io.modelcontextprotocol/serverInfo': info } } : result });
  if (rpc.method === 'server/discover') return respond({ supportedVersions: [MODERN], capabilities });
  if (rpc.method === 'initialize') return respond({ protocolVersion: VERSIONS.includes(String(params.protocolVersion)) ? params.protocolVersion : VERSIONS[0], serverInfo: info, capabilities });
  if (rpc.method === 'ping') return respond({});
  if (rpc.method === 'tools/list') return respond({ tools: auth.scope === 'read' || env.HUB_AUDIENCE === 'personal' || env.HUB_INTAKE_ENABLED !== 'true' ? [] : tools });
  if (rpc.method === 'tools/call') {
    if (auth.scope === 'read' || env.HUB_AUDIENCE === 'personal' || env.HUB_INTAKE_ENABLED !== 'true') return rpcError(request, rpc.id, -32001, 'FORBIDDEN', 403);
    if (typeof params.name !== 'string' || !toolNames.includes(params.name as typeof toolNames[number]) || !object(params.arguments)) return rpcError(request, rpc.id, -32602, 'INVALID_PARAMS', 400);
    try {
      const context = { db: env.HUB_DB, auth, org: env.HUB_ORG };
      const result = params.name === 'submit_lesson' ? await submitLesson(params.arguments as SubmitInput, context) : params.name === 'confirm_lesson' ? await confirmLesson(params.arguments as ConfirmInput, context) : await reportFailure(params.arguments as ReportInput, context);
      return respond({ content: [{ type: 'text', text: JSON.stringify(result) }], structuredContent: result as Record<string, unknown>, isError: false });
    } catch (error) { const code = error instanceof InputError ? error.code : object(error) && typeof error.code === 'string' ? error.code : 'INTAKE_FAILED'; const status = error instanceof InputError ? error.status : object(error) && typeof error.status === 'number' ? error.status : 500; return rpcError(request, rpc.id, -32602, code, status); }
  }
  if (!['skills/list', 'skills/get', 'resources/list', 'resources/read', 'resources/directory/read'].includes(rpc.method)) return rpcError(request, rpc.id, -32601, 'METHOD_NOT_FOUND', modern ? 404 : 200);
  try {
    if (params._meta !== undefined && !object(params._meta)) throw new ReleaseError('Invalid request metadata', 400);
    const pin = meta['io.gisul/commit'];
    if (pin !== undefined && (typeof pin !== 'string' || !/^[a-f0-9]{40}$/.test(pin))) throw new ReleaseError('Invalid pin', 400);
    const snapshot = await visibleSnapshot(env.SKILLS_BUCKET, { audience: env.HUB_AUDIENCE, pin, includeTrial: meta['io.portwright/include_trial'] === true });
    const _meta = { release: snapshot.identity.release, commit: snapshot.identity.commit };
    if (rpc.method === 'skills/list') {
      if (params.cursor !== undefined) throw new ReleaseError('Catalog does not paginate', 400);
      return respond({ skills: snapshot.inventory.skills, _meta });
    }
    if (rpc.method === 'resources/list') return respond({ resources: [...snapshot.files.keys()].sort().map(uri => ({ uri, name: decodeURIComponent(uri.split('/').at(-1)!), mimeType: mimeType(uri) })), _meta });
    const uri = canonicalUri(params.uri);
    if (rpc.method === 'skills/get') {
      const target = resolveAlias(snapshot.inventory, uri);
      const skill = snapshot.inventory.skills.find(item => item.uri === target)!;
      return respond({ skill, _meta: { ..._meta, ...(target !== uri ? { movedFrom: uri } : {}) } });
    }
    if (rpc.method === 'resources/directory/read') return respond({ resources: readDirectory(snapshot, uri), _meta });
    return respond({ contents: [await readResource(env.SKILLS_BUCKET, snapshot, uri)], _meta });
  } catch (error) {
    const status = error instanceof ReleaseError ? error.status : 503;
    return rpcError(request, rpc.id, status === 404 || status === 410 || status === 400 ? -32602 : -32603, status === 410 ? 'REVISION_RECALLED' : status === 404 ? 'NOT_FOUND' : status === 400 ? 'INVALID_PARAMS' : 'DELIVERY_UNAVAILABLE', modern ? status : 200);
  }
}
