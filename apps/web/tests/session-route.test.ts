import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { GET } from "../src/app/api/d18/session/route.ts";

const ORIGINAL_ENV = { ...process.env };
const ORIGINAL_FETCH = globalThis.fetch;
const RELEASE_ID = "a".repeat(64);

function configureSignedSession() {
  const dir = mkdtempSync(join(tmpdir(), "d18-session-route-"));
  const registryPath = join(dir, "registry.json");
  const keyPath = join(dir, "key");
  writeFileSync(
    registryPath,
    JSON.stringify({
      contract: "d18_frontend_session_registry/v1",
      sessions: [
        {
          session_id: "browser-pilot",
          project_namespace: "pilot-a",
          backend_token: "backend-token-not-for-browser",
          expires_at: "2099-08-11T09:00:00Z",
        },
      ],
    }),
    { mode: 0o600 },
  );
  writeFileSync(keyPath, "0123456789abcdef0123456789abcdef", { mode: 0o600 });
  chmodSync(registryPath, 0o600);
  chmodSync(keyPath, 0o600);
  process.env.D18_BASE_URL = "http://127.0.0.1:8080";
  process.env.D18_AUTH_MODE = "signed_session";
  process.env.D18_SESSION_REGISTRY_FILE = registryPath;
  process.env.D18_SESSION_SIGNING_KEY_FILE = keyPath;
  process.env.D18_LOCAL_PILOT_SESSION_ID = "browser-pilot";
  process.env.D18_RELEASE_ID = RELEASE_ID;
  process.env.D18_FRONTEND_BUILD_ID = "frontend-build-a";
  globalThis.fetch = async () => Response.json({
    status: "ready",
    release: {
      contract: "d18.runtime_release_identity/v1",
      release_id: RELEASE_ID,
      frontend_build_id: "frontend-build-a",
    },
  });
}

test.afterEach(() => {
  process.env = { ...ORIGINAL_ENV };
  globalThis.fetch = ORIGINAL_FETCH;
});

test("loopback session endpoint issues a scoped HttpOnly cookie without a backend token", async () => {
  configureSignedSession();
  const response = await GET(
    new Request("http://localhost:3000/api/d18/session", {
      headers: { host: "localhost:3000" },
    }),
  );
  const body = await response.json();
  const cookie = response.headers.get("set-cookie") ?? "";

  assert.equal(response.status, 200);
  assert.deepEqual(body, {
    authenticated: true,
    auth_mode: "signed_session",
    project_namespace: "pilot-a",
    expires_at: "2099-08-11T09:00:00Z",
    release_id: RELEASE_ID,
    frontend_build_id: "frontend-build-a",
  });
  assert.match(cookie, /^d18_session=/);
  assert.match(cookie, /HttpOnly/i);
  assert.match(cookie, /SameSite=Strict/i);
  assert.doesNotMatch(cookie + JSON.stringify(body), /backend-token-not-for-browser/);
});

test("session issuance fails closed away from loopback", async () => {
  configureSignedSession();
  const response = await GET(
    new Request("https://example.com/api/d18/session", {
      headers: { host: "example.com" },
    }),
  );
  assert.equal(response.status, 403);
  assert.equal(response.headers.get("set-cookie"), null);
  assert.doesNotMatch(await response.text(), /backend-token-not-for-browser/);
});

test("fixture mode remains available without session secrets", async () => {
  delete process.env.D18_BASE_URL;
  delete process.env.D18_AUTH_MODE;
  const response = await GET(
    new Request("http://localhost:3000/api/d18/session", {
      headers: { host: "localhost:3000" },
    }),
  );
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    authenticated: true,
    auth_mode: "fixture",
    project_namespace: "r",
    expires_at: null,
    release_id: "fixture",
    frontend_build_id: "fixture",
  });
});

test("signed session fails closed when frontend and backend releases differ", async () => {
  configureSignedSession();
  globalThis.fetch = async () => Response.json({
    status: "ready",
    release: { release_id: "b".repeat(64) },
  });
  const response = await GET(
    new Request("http://localhost:3000/api/d18/session", {
      headers: { host: "localhost:3000" },
    }),
  );
  assert.equal(response.status, 503);
  assert.equal((await response.json()).code, "release_mismatch");
  assert.equal(response.headers.get("set-cookie"), null);
});

test("live session endpoint rejects an absent authentication mode even when a token exists", async () => {
  process.env.D18_BASE_URL = "http://127.0.0.1:8080";
  process.env.D18_TOKEN = "legacy-static-token";
  delete process.env.D18_AUTH_MODE;
  const response = await GET(
    new Request("http://localhost:3000/api/d18/session", {
      headers: { host: "localhost:3000" },
    }),
  );
  assert.equal(response.status, 503);
  assert.equal((await response.json()).code, "session_auth_unavailable");
});
