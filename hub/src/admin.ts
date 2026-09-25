import { jsonResponse } from './http.ts';
import { publisherFetch } from './release-publisher.ts';
import { readRecalls, writeRecalls } from './recalls.ts';
import { ReleaseError } from './r2-objects.ts';
import { HubError, manageToken } from './auth.ts';
import type { AuthContext } from './auth.ts';
import type { TransitionInput } from './intake.ts';
import type { ManageTokenInput } from './auth.ts';
import { listIntake, listEvents, transitionIntake } from './intake.ts';
import { budgetSummary, budgetRequest } from './budget.ts';
import { safeJson, InputError } from './direct.ts';
import type { HubEnv } from './index.ts';

export async function adminFetch(request: Request, env: HubEnv, auth: AuthContext, cap: number): Promise<Response> {
  if (auth.scope !== 'operator') return jsonResponse(request, { code: 'FORBIDDEN' }, 403);
  const url = new URL(request.url);
  const path = url.pathname;
  if (env.HUB_AUDIENCE === 'personal' && ['/admin/intake','/admin/events','/admin/budget','/admin/recalls'].includes(path)) return jsonResponse(request, { code: 'NOT_FOUND' }, 404);
  if (request.method === 'POST' && ['/admin/intake','/admin/tokens','/admin/budget','/admin/recalls'].includes(path) &&
      !/^application\/json(?:\s*;|$)/i.test(request.headers.get('content-type') ?? '')) return jsonResponse(request, { code: 'INVALID_CONTENT_TYPE' }, 415);
  const context = { db: env.HUB_DB, auth, org: env.HUB_ORG };
  try {
    if ((path === '/admin/intake' || path === '/admin/events') && request.method === 'GET') {
      const query = Object.fromEntries(url.searchParams);
      if (query.limit !== undefined && !/^[1-9][0-9]*$/.test(query.limit)) throw new InputError('INVALID_PARAMS');
      const params = { ...query, ...(query.limit !== undefined ? { limit: Number(query.limit) } : {}) };
      return jsonResponse(request, path === '/admin/intake' ? await listIntake(params, context) : await listEvents(params, context));
    }
    if (path === '/admin/intake' && request.method === 'POST') return jsonResponse(request, await transitionIntake(await safeJson(request) as TransitionInput, context));
    if (path === '/admin/tokens' && request.method === 'POST') return jsonResponse(request, await manageToken(await safeJson(request) as ManageTokenInput, context));
    if (path === '/admin/budget' && request.method === 'GET') return jsonResponse(request, await budgetSummary({ ...context, cap }));
    if (path === '/admin/budget' && request.method === 'POST') return jsonResponse(request, await budgetRequest(await safeJson(request), { ...context, cap }));
    if (path === '/admin/recalls' && request.method === 'GET') {
      const snapshot = await readRecalls(env.SKILLS_BUCKET, env.HUB_AUDIENCE);
      const { etag, ...value } = snapshot;
      return jsonResponse(request, { snapshot: value, etag });
    }
    if (path === '/admin/recalls' && request.method === 'POST') {
      const body = await safeJson(request, 1024 * 1024, 1024 * 1024);
      if (!body || typeof body !== 'object' || Array.isArray(body) || Object.keys(body).sort().join(',') !== 'expected_etag,snapshot') throw new InputError('INVALID_PARAMS');
      const input = body as { snapshot: Parameters<typeof writeRecalls>[1]; expected_etag: string | null };
      if (input.snapshot?.audience !== env.HUB_AUDIENCE || (input.expected_etag !== null && typeof input.expected_etag !== 'string')) throw new InputError('INVALID_PARAMS');
      return jsonResponse(request, await writeRecalls(env.SKILLS_BUCKET, input.snapshot, input.expected_etag));
    }
    if (['/admin/current','/admin/verify','/admin/promote','/admin/rollback'].includes(path) || path.startsWith('/admin/releases/')) return publisherFetch(request, { bucket: env.SKILLS_BUCKET, audience: env.HUB_AUDIENCE, operatorHash: auth.hash });
    return jsonResponse(request, { code: 'NOT_FOUND' }, 404);
  } catch (error) {
    if (error instanceof HubError || error instanceof InputError) return jsonResponse(request, { code: error.code }, error.status);
    if (error instanceof ReleaseError) return jsonResponse(request, { code: error.status === 404 ? 'NOT_FOUND' : 'DELIVERY_UNAVAILABLE' }, error.status);
    return jsonResponse(request, { code: 'INTERNAL' }, 500);
  }
}
