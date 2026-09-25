import { jsonResponse, corsHeaders } from './http.ts';
import { authenticate, HubError } from './auth.ts';
import { directFetch } from './direct.ts';
import { adminFetch } from './admin.ts';

export type HubEnv = {
  HUB_DB: D1Database;
  SKILLS_BUCKET: R2Bucket;
  HUB_AUDIENCE: 'personal' | 'public' | 'company';
  HUB_ORG: string;
  HUB_INTAKE_ENABLED: string;
  HUB_MONTHLY_CAP_MICROUSD: string;
};

export default {
  async fetch(request: Request, env: HubEnv): Promise<Response> {
    const audience = env.HUB_AUDIENCE;
    const cap = Number(env.HUB_MONTHLY_CAP_MICROUSD);
    if (!['personal', 'public', 'company'].includes(audience) || !env.HUB_ORG || (audience === 'personal' && env.HUB_INTAKE_ENABLED === 'true') ||
        !/^\d+$/.test(env.HUB_MONTHLY_CAP_MICROUSD) || !Number.isSafeInteger(cap) || cap > 10_000_000 || !env.HUB_DB || !env.SKILLS_BUCKET) {
      return jsonResponse(request, { code: 'CONFIG_INVALID' }, 503);
    }
    const pathname = new URL(request.url).pathname;
    if (pathname === '/healthz' && request.method === 'GET') return jsonResponse(request, { ok: true, service: 'portwright-hub' });
    if (pathname === '/mcp' && request.method === 'OPTIONS') return new Response(null, { status: 204, headers: corsHeaders(request) });
    if (pathname !== '/mcp' && pathname !== '/sync' && !pathname.startsWith('/admin/')) return jsonResponse(request, { code: 'NOT_FOUND' }, 404);
    try {
      const auth = await authenticate(request, env);
      if (pathname.startsWith('/admin/')) return await adminFetch(request, env, auth, cap);
      return await directFetch(request, env, auth);
    } catch (error) {
      if (error instanceof HubError) return jsonResponse(request, { code: error.code }, error.status);
      return jsonResponse(request, { code: 'INTERNAL' }, 500);
    }
  },
} satisfies ExportedHandler<HubEnv>;
