import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  createSignedSessionCookie,
  configuredSessionTokenResolver,
  isLoopbackHost,
  resolveSignedSession,
  sessionTokenResolver,
} from "../src/server/d18-session.ts";

const NOW = new Date("2026-08-11T08:00:00Z");

function secureFixture(expiresAt = "2026-08-11T09:00:00Z") {
  const dir = mkdtempSync(join(tmpdir(), "d18-session-"));
  const registryPath = join(dir, "registry.json");
  const keyPath = join(dir, "signing-key");
  writeFileSync(
    registryPath,
    JSON.stringify({
      contract: "d18_frontend_session_registry/v1",
      sessions: [
        {
          session_id: "local-pilot",
          project_namespace: "pilot-a",
          backend_token: "backend-secret-token",
          expires_at: expiresAt,
        },
      ],
    }),
    { mode: 0o600 },
  );
  writeFileSync(keyPath, "0123456789abcdef0123456789abcdef", { mode: 0o600 });
  chmodSync(registryPath, 0o600);
  chmodSync(keyPath, 0o600);
  return { registryPath, keyPath };
}

test("signed session resolves only its project namespace without exposing it in payload", () => {
  const fixture = secureFixture();
  const cookie = createSignedSessionCookie({
    ...fixture,
    sessionId: "local-pilot",
    now: NOW,
  });
  const resolved = resolveSignedSession({
    ...fixture,
    cookieValue: cookie,
    expectedProjectId: "pilot-a-study-1",
    now: NOW,
  });

  assert.equal(resolved.backendToken, "backend-secret-token");
  assert.equal(resolved.projectNamespace, "pilot-a");
  assert.equal(resolved.sessionId, "local-pilot");
  assert.doesNotMatch(cookie, /backend-secret-token/);
  assert.throws(
    () =>
      resolveSignedSession({
        ...fixture,
        cookieValue: cookie,
        expectedProjectId: "pilot-b-study-1",
        now: NOW,
      }),
    /project_scope_mismatch/,
  );
});

test("tampered, expired, and insecure session material fails closed", () => {
  const fixture = secureFixture();
  const cookie = createSignedSessionCookie({
    ...fixture,
    sessionId: "local-pilot",
    now: NOW,
  });
  assert.throws(
    () =>
      resolveSignedSession({
        ...fixture,
        cookieValue: `${cookie.slice(0, -1)}x`,
        now: NOW,
      }),
    /invalid_signature/,
  );

  const expired = secureFixture("2026-08-11T07:59:59Z");
  assert.throws(
    () =>
      createSignedSessionCookie({
        ...expired,
        sessionId: "local-pilot",
        now: NOW,
      }),
    /session_expired/,
  );

  chmodSync(fixture.registryPath, 0o644);
  assert.throws(
    () =>
      createSignedSessionCookie({
        ...fixture,
        sessionId: "local-pilot",
        now: NOW,
      }),
    /insecure_file_mode/,
  );
});

test("configured resolver supports signed sessions and keeps static mode explicit", () => {
  const fixture = secureFixture();
  const cookie = createSignedSessionCookie({
    ...fixture,
    sessionId: "local-pilot",
    now: NOW,
  });

  assert.equal(
    sessionTokenResolver(
      { projectId: "pilot-a-study-1", sessionId: cookie },
      {
        authMode: "signed_session",
        ...fixture,
        now: NOW,
      },
    ),
    "backend-secret-token",
  );
  assert.equal(
    sessionTokenResolver(
      {},
      { authMode: "local_static", staticToken: "static-token", now: NOW },
    ),
    "static-token",
  );
  assert.equal(
    sessionTokenResolver({}, { authMode: "signed_session", ...fixture, now: NOW }),
    undefined,
  );
});

test("live resolver fails closed when the authentication mode is absent or unknown", () => {
  const originalMode = process.env.D18_AUTH_MODE;
  const originalToken = process.env.D18_TOKEN;
  try {
    process.env.D18_TOKEN = "static-token-that-must-not-be-used";
    delete process.env.D18_AUTH_MODE;
    assert.equal(configuredSessionTokenResolver({}), undefined);
    process.env.D18_AUTH_MODE = "unknown";
    assert.equal(configuredSessionTokenResolver({}), undefined);
  } finally {
    if (originalMode === undefined) delete process.env.D18_AUTH_MODE;
    else process.env.D18_AUTH_MODE = originalMode;
    if (originalToken === undefined) delete process.env.D18_TOKEN;
    else process.env.D18_TOKEN = originalToken;
  }
});

test("local pilot issuance accepts loopback hosts only", () => {
  assert.equal(isLoopbackHost("localhost:3000"), true);
  assert.equal(isLoopbackHost("127.0.0.1:3000"), true);
  assert.equal(isLoopbackHost("[::1]:3000"), true);
  assert.equal(isLoopbackHost("example.com"), false);
  assert.equal(isLoopbackHost("localhost.example.com"), false);
});

test("session expiry accepts the microsecond precision emitted by the Python launcher", () => {
  const fixture = secureFixture("2026-08-11T09:00:00.123456Z");
  const cookie = createSignedSessionCookie({
    ...fixture,
    sessionId: "local-pilot",
    now: NOW,
  });
  assert.equal(
    resolveSignedSession({ ...fixture, cookieValue: cookie, now: NOW }).expiresAt,
    "2026-08-11T09:00:00.123456Z",
  );
});
