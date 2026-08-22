/**
 * D-18 代理层服务端工具。
 * token 只存在于服务端 env（D18_BASE_URL / D18_TOKEN），绝不进入浏览器包。
 * D18_BASE_URL 未配置时降级为 fixture 回放（fixture-engine）。
 */

export function d18Mode(): "live" | "fixture" {
  return process.env.D18_BASE_URL ? "live" : "fixture";
}

export type D18Fetch = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

export interface D18AuthContext {
  projectId?: string;
  sessionId?: string;
}

export type D18TokenResolver = (
  context: Readonly<D18AuthContext>,
) => string | undefined;

/** Local single-user default; production can inject a trusted session/project resolver. */
export const environmentTokenResolver: D18TokenResolver = () =>
  process.env.D18_TOKEN;

function unavailableResponse(): Response {
  return Response.json(
    {
      code: "upstream_unavailable",
      detail: "后端暂时不可用，请稍后重试。",
      retryable: true,
    },
    { status: 503 },
  );
}

function upstreamFailureResponse(status: number): Response {
  return Response.json(
    {
      code: "upstream_request_failed",
      detail: `后端未接受该请求（${status}）。`,
      retryable: status === 408 || status === 429 || status >= 500,
    },
    { status },
  );
}

function releaseMismatchResponse(): Response {
  return Response.json(
    {
      code: "runtime_release_mismatch",
      detail: "前端与后端版本不一致，请重新启动完整服务。",
      retryable: false,
    },
    { status: 503 },
  );
}

function releaseMatches(upstream: Response): boolean {
  const expected = process.env.D18_RELEASE_ID;
  return !expected || upstream.headers.get("x-d18-release-id") === expected;
}

function upstreamUrl(path: string): string {
  const base = process.env.D18_BASE_URL!.replace(/\/+$/, "");
  return `${base}${path}`;
}

function authHeaders(
  extra: Record<string, string> | undefined,
  resolver: D18TokenResolver,
  context: D18AuthContext,
): Headers {
  const headers = new Headers(extra);
  const token = resolver(context);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const releaseId = process.env.D18_RELEASE_ID;
  if (releaseId) headers.set("X-D18-Release-ID", releaseId);
  return headers;
}

/** JSON 转发：成功响应透传；失败响应只保留状态分类，绝不转发内部正文。 */
export async function forwardJson(
  path: string,
  init: {
    method: string;
    body?: string;
    headers?: Record<string, string>;
    authContext?: D18AuthContext;
  },
  fetcher: D18Fetch = globalThis.fetch,
  tokenResolver: D18TokenResolver = environmentTokenResolver,
): Promise<Response> {
  let upstream: Response;
  try {
    upstream = await fetcher(upstreamUrl(path), {
      method: init.method,
      headers: authHeaders(init.headers, tokenResolver, init.authContext ?? {}),
      body: init.body,
      cache: "no-store",
    });
  } catch {
    return unavailableResponse();
  }
  let text: string;
  try {
    text = await upstream.text();
  } catch {
    return unavailableResponse();
  }
  if (!releaseMatches(upstream)) return releaseMismatchResponse();
  if (!upstream.ok) return upstreamFailureResponse(upstream.status);
  return new Response(text, {
    status: upstream.status,
    headers: {
      "Content-Type":
        upstream.headers.get("content-type") ?? "application/json",
    },
  });
}

/** SSE / 二进制流转发：body 原样流式透传 */
export async function forwardStream(
  path: string,
  init: {
    method: string;
    body?: string;
    headers?: Record<string, string>;
    authContext?: D18AuthContext;
  },
  fetcher: D18Fetch = globalThis.fetch,
  tokenResolver: D18TokenResolver = environmentTokenResolver,
): Promise<Response> {
  let upstream: Response;
  try {
    upstream = await fetcher(upstreamUrl(path), {
      method: init.method,
      headers: authHeaders(init.headers, tokenResolver, init.authContext ?? {}),
      body: init.body,
      cache: "no-store",
    });
  } catch {
    return unavailableResponse();
  }
  if (!releaseMatches(upstream)) return releaseMismatchResponse();
  if (!upstream.ok) return upstreamFailureResponse(upstream.status);
  const headers = new Headers();
  const contentType = upstream.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);
  const disposition = upstream.headers.get("content-disposition");
  if (disposition) headers.set("Content-Disposition", disposition);
  for (const name of ["x-content-type-options", "content-security-policy"] as const) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  headers.set(
    "Cache-Control",
    upstream.headers.get("cache-control") ?? "no-cache, no-transform",
  );
  return new Response(upstream.body, { status: upstream.status, headers });
}

const uploadRequestHeaders = [
  "content-range",
  "content-type",
  "x-part-sha256",
] as const;

/** Raw upload forwarding keeps bytes streamed and admits only the multipart contract headers. */
export async function forwardRequestStream(
  path: string,
  request: Request,
  options: { method: "PUT"; authContext: D18AuthContext },
  fetcher: D18Fetch = globalThis.fetch,
  tokenResolver: D18TokenResolver = environmentTokenResolver,
): Promise<Response> {
  const allowed = new Headers();
  for (const name of uploadRequestHeaders) {
    const value = request.headers.get(name);
    if (value) allowed.set(name, value);
  }
  if (options.authContext.projectId) {
    allowed.set("X-D18-Project-ID", options.authContext.projectId);
  }
  const headers = authHeaders(
    Object.fromEntries(allowed.entries()),
    tokenResolver,
    options.authContext,
  );
  let upstream: Response;
  try {
    upstream = await fetcher(upstreamUrl(path), {
      method: options.method,
      headers,
      body: request.body,
      cache: "no-store",
      duplex: "half",
    } as RequestInit & { duplex: "half" });
  } catch {
    return unavailableResponse();
  }
  if (!releaseMatches(upstream)) return releaseMismatchResponse();
  if (!upstream.ok) return upstreamFailureResponse(upstream.status);
  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type":
        upstream.headers.get("content-type") ?? "application/json",
      "Cache-Control": "no-store",
    },
  });
}

export function jsonResponse(body: unknown, status = 200): Response {
  return Response.json(body, { status });
}
