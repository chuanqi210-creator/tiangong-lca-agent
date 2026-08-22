import assert from "node:assert/strict";
import test from "node:test";

import {
  connectD18Session,
  ensureD18Session,
  researchIdForNamespace,
} from "../src/lib/session-client.ts";

test("browser research identities are deterministically scoped to the signed namespace", () => {
  assert.equal(researchIdForNamespace("pilot-a", 1_723_369_600_000), "pilot-a-lzpdrhts");
  assert.throws(() => researchIdForNamespace("../escape", 1), /invalid project namespace/);
  assert.throws(() => researchIdForNamespace("Pilot", 1), /invalid project namespace/);
});

test("browser session bootstrap returns only the public project namespace", async () => {
  let called = "";
  const session = await ensureD18Session(async (input, init) => {
    called = String(input);
    assert.equal(init?.credentials, "same-origin");
    return Response.json({
      authenticated: true,
      auth_mode: "signed_session",
      project_namespace: "pilot-a",
      expires_at: "2099-08-11T09:00:00Z",
      release_id: "a".repeat(64),
      frontend_build_id: "frontend-build-a",
    });
  });
  assert.equal(called, "/api/d18/session");
  assert.deepEqual(session, {
    authMode: "signed_session",
    projectNamespace: "pilot-a",
    expiresAt: "2099-08-11T09:00:00Z",
    releaseId: "a".repeat(64),
    frontendBuildId: "frontend-build-a",
  });
});

test("browser session bootstrap fails closed on malformed or rejected identity", async () => {
  await assert.rejects(
    ensureD18Session(async () => Response.json({ authenticated: false }, { status: 403 })),
    /session unavailable/,
  );
  await assert.rejects(
    ensureD18Session(async () => Response.json({ authenticated: true, project_namespace: "../x" })),
    /invalid session response/,
  );
});

test("page connection probe exposes connected and unavailable states", async () => {
  const connected = await connectD18Session(async () =>
    Response.json({
      authenticated: true,
      auth_mode: "signed_session",
      project_namespace: "pilot-a",
      expires_at: "2099-08-11T09:00:00Z",
      release_id: "a".repeat(64),
      frontend_build_id: "frontend-build-a",
    }),
  );
  assert.equal(connected.status, "connected");
  assert.equal(connected.session?.projectNamespace, "pilot-a");
  assert.equal(connected.session?.releaseId, "a".repeat(64));

  const unavailable = await connectD18Session(async () => {
    throw new TypeError("network unavailable");
  });
  assert.deepEqual(unavailable, { status: "unavailable", session: null });
});
