export function corsHeaders(request: Request, existingVary: string | null = null): HeadersInit {
  const origin = request.headers.get("origin");
  if (!origin) return {};
  let vary = existingVary ?? "";
  const fields = vary.split(",").map(field => field.trim().toLowerCase());
  if (!fields.includes("*") && !fields.includes("origin")) vary = vary ? `${vary}, Origin` : "Origin";
  return {
    "access-control-allow-origin": origin,
    "access-control-allow-methods": "GET, POST, OPTIONS",
    "access-control-allow-headers": "authorization, content-type, mcp-protocol-version, mcp-session-id, mcp-method, mcp-name",
    "access-control-max-age": "86400",
    vary,
  };
}


export function jsonResponse(request: Request, value: unknown, status = 200, extra?: HeadersInit): Response {
  const headers = new Headers({ "content-type": "application/json", "cache-control": "no-store", ...corsHeaders(request) });
  new Headers(extra).forEach((value, key) => headers.set(key, value));
  return new Response(value === undefined ? null : JSON.stringify(value), { status, headers });
}

export class BodyTooLargeError extends Error {
  constructor() { super("Request body is too large"); }
}

export async function readBody(request: Pick<Request, "body">, limit: number): Promise<ArrayBuffer> {
  const reader = request.body?.getReader();
  if (!reader) return new ArrayBuffer(0);
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) { await reader.cancel(); throw new BodyTooLargeError(); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.byteLength; }
  return result.buffer;
}
