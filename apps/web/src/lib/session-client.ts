const SAFE_NAMESPACE = /^[a-z][a-z0-9-]{0,31}$/;
const SAFE_RELEASE = /^(?:fixture|[0-9a-f]{64})$/;

export interface BrowserSession {
  authMode: "signed_session" | "local_static" | "fixture";
  projectNamespace: string;
  expiresAt: string | null;
  releaseId: string;
  frontendBuildId: string;
}

export type D18Connection =
  | { status: "connecting"; session: null }
  | { status: "connected"; session: BrowserSession }
  | { status: "unavailable"; session: null };

type SessionFetch = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

export function researchIdForNamespace(
  projectNamespace: string,
  now = Date.now(),
): string {
  if (!SAFE_NAMESPACE.test(projectNamespace)) {
    throw new Error("invalid project namespace");
  }
  return `${projectNamespace}-${now.toString(36)}`;
}

/** Establishes an HttpOnly BFF session and returns only its public namespace. */
export async function ensureD18Session(
  fetcher: SessionFetch = globalThis.fetch,
): Promise<BrowserSession> {
  const response = await fetcher("/api/d18/session", {
    method: "GET",
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!response.ok) throw new Error("session unavailable");
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new Error("invalid session response");
  }
  if (!body || typeof body !== "object") {
    throw new Error("invalid session response");
  }
  const data = body as Record<string, unknown>;
  const validMode =
    data.auth_mode === "signed_session" ||
    data.auth_mode === "local_static" ||
    data.auth_mode === "fixture";
  if (
    data.authenticated !== true ||
    !validMode ||
    typeof data.project_namespace !== "string" ||
    !SAFE_NAMESPACE.test(data.project_namespace) ||
    !(data.expires_at === null || typeof data.expires_at === "string")
    || typeof data.release_id !== "string"
    || !SAFE_RELEASE.test(data.release_id)
    || typeof data.frontend_build_id !== "string"
    || !data.frontend_build_id
  ) {
    throw new Error("invalid session response");
  }
  return {
    authMode: data.auth_mode as BrowserSession["authMode"],
    projectNamespace: data.project_namespace,
    expiresAt: data.expires_at as string | null,
    releaseId: data.release_id,
    frontendBuildId: data.frontend_build_id,
  };
}

/** Converts the fail-closed session handshake into a presentation-safe state. */
export async function connectD18Session(
  fetcher: SessionFetch = globalThis.fetch,
): Promise<D18Connection> {
  try {
    return { status: "connected", session: await ensureD18Session(fetcher) };
  } catch {
    return { status: "unavailable", session: null };
  }
}
