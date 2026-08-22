import {
  SESSION_COOKIE_NAME,
  SessionResolutionError,
  createSignedSessionCookie,
  isLoopbackHost,
  resolveSignedSession,
  sessionCookieFromRequest,
} from "../../../../server/d18-session.ts";

function response(body: unknown, status = 200, headers?: HeadersInit): Response {
  return Response.json(body, { status, headers });
}

function publicSession(
  authMode: "signed_session" | "local_static" | "fixture",
  projectNamespace: string,
  expiresAt: string | null,
  releaseId: string,
  frontendBuildId: string,
) {
  return {
    authenticated: true,
    auth_mode: authMode,
    project_namespace: projectNamespace,
    expires_at: expiresAt,
    release_id: releaseId,
    frontend_build_id: frontendBuildId,
  };
}

const RELEASE_ID = /^[0-9a-f]{64}$/;

async function requireMatchingRelease(): Promise<
  | { releaseId: string; frontendBuildId: string }
  | { error: "release_unavailable" | "release_mismatch" }
> {
  const baseUrl = process.env.D18_BASE_URL;
  const expectedReleaseId = process.env.D18_RELEASE_ID;
  const frontendBuildId = process.env.D18_FRONTEND_BUILD_ID;
  if (
    !baseUrl ||
    !expectedReleaseId ||
    !RELEASE_ID.test(expectedReleaseId) ||
    !frontendBuildId
  ) {
    return { error: "release_unavailable" };
  }
  try {
    const backend = await fetch(`${baseUrl}/health/ready`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3_000),
    });
    if (!backend.ok) return { error: "release_unavailable" };
    const body = await backend.json() as Record<string, unknown>;
    const release = body.release;
    if (!release || typeof release !== "object") {
      return { error: "release_unavailable" };
    }
    const observed = release as Record<string, unknown>;
    if (
      observed.release_id !== expectedReleaseId ||
      observed.frontend_build_id !== frontendBuildId
    ) {
      return { error: "release_mismatch" };
    }
    return { releaseId: expectedReleaseId, frontendBuildId };
  } catch {
    return { error: "release_unavailable" };
  }
}

/**
 * Local pilot session issuer and session status endpoint.
 * The backend bearer token remains server-side in the 0600 registry.
 * A future OIDC issuer can mint the same signed session contract without
 * changing the BFF resolver or Project Record authorization boundary.
 */
export async function GET(request: Request): Promise<Response> {
  if (!process.env.D18_BASE_URL) {
    return response(publicSession("fixture", "r", null, "fixture", "fixture"));
  }

  const authMode = process.env.D18_AUTH_MODE;
  if (authMode !== "local_static" && authMode !== "signed_session") {
    return response(
      { authenticated: false, code: "session_auth_unavailable" },
      503,
    );
  }
  const release = await requireMatchingRelease();
  if ("error" in release) {
    return response({ authenticated: false, code: release.error }, 503);
  }

  if (authMode === "local_static") {
    if (!process.env.D18_TOKEN) {
      return response(
        { authenticated: false, code: "session_auth_unavailable" },
        503,
      );
    }
    return response(
      publicSession(
        "local_static",
        "r",
        null,
        release.releaseId,
        release.frontendBuildId,
      ),
    );
  }
  const registryPath = process.env.D18_SESSION_REGISTRY_FILE;
  const keyPath = process.env.D18_SESSION_SIGNING_KEY_FILE;
  if (!registryPath || !keyPath) {
    return response(
      { authenticated: false, code: "session_auth_unavailable" },
      503,
    );
  }

  const existing = sessionCookieFromRequest(request);
  if (existing) {
    try {
      const resolved = resolveSignedSession({
        registryPath,
        keyPath,
        cookieValue: existing,
      });
      return response(
        publicSession(
          "signed_session",
          resolved.projectNamespace,
          resolved.expiresAt,
          release.releaseId,
          release.frontendBuildId,
        ),
      );
    } catch (error) {
      if (!(error instanceof SessionResolutionError)) throw error;
      // A stale or invalid cookie may be replaced only by the loopback issuer.
    }
  }

  if (!isLoopbackHost(request.headers.get("host") ?? "")) {
    return response({ authenticated: false, code: "session_issuance_forbidden" }, 403);
  }
  const sessionId = process.env.D18_LOCAL_PILOT_SESSION_ID;
  if (!sessionId) {
    return response(
      { authenticated: false, code: "session_issuer_unavailable" },
      503,
    );
  }

  try {
    const cookieValue = createSignedSessionCookie({
      registryPath,
      keyPath,
      sessionId,
    });
    const resolved = resolveSignedSession({
      registryPath,
      keyPath,
      cookieValue,
    });
    const maxAge = Math.max(
      0,
      Math.floor((Date.parse(resolved.expiresAt) - Date.now()) / 1000),
    );
    return response(
      publicSession(
        "signed_session",
        resolved.projectNamespace,
        resolved.expiresAt,
        release.releaseId,
        release.frontendBuildId,
      ),
      200,
      {
        "Set-Cookie": `${SESSION_COOKIE_NAME}=${cookieValue}; Path=/; Max-Age=${maxAge}; HttpOnly; SameSite=Strict`,
        "Cache-Control": "no-store",
      },
    );
  } catch (error) {
    if (!(error instanceof SessionResolutionError)) throw error;
    return response({ authenticated: false, code: error.code }, 503);
  }
}
