import {
  createHmac,
  timingSafeEqual,
} from "node:crypto";
import { readFileSync, statSync } from "node:fs";

import type {
  D18AuthContext,
  D18TokenResolver,
} from "../lib/d18/proxy.ts";

export const SESSION_COOKIE_NAME = "d18_session";
const REGISTRY_CONTRACT = "d18_frontend_session_registry/v1";
const SAFE_SESSION_ID = /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/;
const SAFE_NAMESPACE = /^[a-z][a-z0-9-]{0,31}$/;

interface SessionRegistryEntry {
  session_id: string;
  project_namespace: string;
  backend_token: string;
  expires_at: string;
}

interface SessionRegistry {
  contract: typeof REGISTRY_CONTRACT;
  sessions: SessionRegistryEntry[];
}

interface CookiePayload {
  v: 1;
  session_id: string;
  project_namespace: string;
  expires_at: string;
}

export interface ResolvedSession {
  sessionId: string;
  projectNamespace: string;
  backendToken: string;
  expiresAt: string;
}

export interface SessionFileOptions {
  registryPath: string;
  keyPath: string;
  now?: Date;
}

export interface SessionRuntimeOptions {
  authMode: "signed_session" | "local_static";
  registryPath?: string;
  keyPath?: string;
  staticToken?: string;
  now?: Date;
}

export class SessionResolutionError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "SessionResolutionError";
    this.code = code;
  }
}

function fail(code: string): never {
  throw new SessionResolutionError(code);
}

function readPrivateFile(path: string): Buffer {
  let stat;
  try {
    stat = statSync(path);
  } catch {
    return fail("session_file_unavailable");
  }
  if (!stat.isFile()) fail("session_file_not_regular");
  if (typeof process.getuid === "function" && stat.uid !== process.getuid()) {
    fail("session_file_wrong_owner");
  }
  if ((stat.mode & 0o077) !== 0) fail("insecure_file_mode");
  try {
    return readFileSync(path);
  } catch {
    return fail("session_file_unavailable");
  }
}

function exactIso(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(value) &&
    !Number.isNaN(Date.parse(value))
  );
}

function loadRegistry(path: string): SessionRegistry {
  let parsed: unknown;
  try {
    parsed = JSON.parse(readPrivateFile(path).toString("utf8"));
  } catch (error) {
    if (error instanceof SessionResolutionError) throw error;
    return fail("invalid_session_registry");
  }
  if (!parsed || typeof parsed !== "object") fail("invalid_session_registry");
  const candidate = parsed as Partial<SessionRegistry>;
  if (
    candidate.contract !== REGISTRY_CONTRACT ||
    !Array.isArray(candidate.sessions)
  ) {
    fail("invalid_session_registry");
  }
  const seenSessions = new Set<string>();
  const seenNamespaces = new Set<string>();
  for (const raw of candidate.sessions) {
    if (!raw || typeof raw !== "object") fail("invalid_session_registry");
    const entry = raw as Partial<SessionRegistryEntry>;
    if (
      typeof entry.session_id !== "string" ||
      !SAFE_SESSION_ID.test(entry.session_id) ||
      typeof entry.project_namespace !== "string" ||
      !SAFE_NAMESPACE.test(entry.project_namespace) ||
      typeof entry.backend_token !== "string" ||
      entry.backend_token.length < 16 ||
      !exactIso(entry.expires_at)
    ) {
      fail("invalid_session_registry");
    }
    if (
      seenSessions.has(entry.session_id) ||
      seenNamespaces.has(entry.project_namespace)
    ) {
      fail("duplicate_session_scope");
    }
    seenSessions.add(entry.session_id);
    seenNamespaces.add(entry.project_namespace);
  }
  return candidate as SessionRegistry;
}

function loadSigningKey(path: string): Buffer {
  const key = Buffer.from(readPrivateFile(path).toString("utf8").trim(), "utf8");
  if (key.length < 32) fail("invalid_signing_key");
  return key;
}

function entryFor(
  registry: SessionRegistry,
  sessionId: string,
  now: Date,
): SessionRegistryEntry {
  const entry = registry.sessions.find((item) => item.session_id === sessionId);
  if (!entry) fail("unknown_session");
  if (Date.parse(entry.expires_at) <= now.getTime()) fail("session_expired");
  return entry;
}

function encodePayload(payload: CookiePayload): string {
  return Buffer.from(JSON.stringify(payload), "utf8").toString("base64url");
}

function signature(encodedPayload: string, key: Buffer): string {
  return createHmac("sha256", key).update(encodedPayload).digest("base64url");
}

function decodePayload(encoded: string): CookiePayload {
  let parsed: unknown;
  try {
    parsed = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8"));
  } catch {
    return fail("invalid_session_payload");
  }
  if (!parsed || typeof parsed !== "object") fail("invalid_session_payload");
  const payload = parsed as Partial<CookiePayload>;
  if (
    payload.v !== 1 ||
    typeof payload.session_id !== "string" ||
    !SAFE_SESSION_ID.test(payload.session_id) ||
    typeof payload.project_namespace !== "string" ||
    !SAFE_NAMESPACE.test(payload.project_namespace) ||
    !exactIso(payload.expires_at)
  ) {
    fail("invalid_session_payload");
  }
  return payload as CookiePayload;
}

export function createSignedSessionCookie(
  options: SessionFileOptions & { sessionId: string },
): string {
  const now = options.now ?? new Date();
  const entry = entryFor(loadRegistry(options.registryPath), options.sessionId, now);
  const payload: CookiePayload = {
    v: 1,
    session_id: entry.session_id,
    project_namespace: entry.project_namespace,
    expires_at: entry.expires_at,
  };
  const encoded = encodePayload(payload);
  return `${encoded}.${signature(encoded, loadSigningKey(options.keyPath))}`;
}

export function resolveSignedSession(
  options: SessionFileOptions & {
    cookieValue: string;
    expectedProjectId?: string;
  },
): ResolvedSession {
  const [encoded, suppliedSignature, ...extra] = options.cookieValue.split(".");
  if (!encoded || !suppliedSignature || extra.length) fail("invalid_session_cookie");
  const expectedSignature = signature(encoded, loadSigningKey(options.keyPath));
  const supplied = Buffer.from(suppliedSignature, "utf8");
  const expected = Buffer.from(expectedSignature, "utf8");
  if (
    supplied.length !== expected.length ||
    !timingSafeEqual(supplied, expected)
  ) {
    fail("invalid_signature");
  }
  const payload = decodePayload(encoded);
  const now = options.now ?? new Date();
  if (Date.parse(payload.expires_at) <= now.getTime()) fail("session_expired");
  const entry = entryFor(loadRegistry(options.registryPath), payload.session_id, now);
  if (
    entry.project_namespace !== payload.project_namespace ||
    entry.expires_at !== payload.expires_at
  ) {
    fail("session_registry_drift");
  }
  if (
    options.expectedProjectId &&
    !options.expectedProjectId.startsWith(`${entry.project_namespace}-`)
  ) {
    fail("project_scope_mismatch");
  }
  return {
    sessionId: entry.session_id,
    projectNamespace: entry.project_namespace,
    backendToken: entry.backend_token,
    expiresAt: entry.expires_at,
  };
}

export function sessionTokenResolver(
  context: Readonly<D18AuthContext>,
  options: SessionRuntimeOptions,
): string | undefined {
  if (options.authMode === "local_static") return options.staticToken;
  if (!context.sessionId || !options.registryPath || !options.keyPath) {
    return undefined;
  }
  try {
    return resolveSignedSession({
      registryPath: options.registryPath,
      keyPath: options.keyPath,
      cookieValue: context.sessionId,
      expectedProjectId: context.projectId,
      now: options.now,
    }).backendToken;
  } catch (error) {
    if (error instanceof SessionResolutionError) return undefined;
    throw error;
  }
}

export const configuredSessionTokenResolver: D18TokenResolver = (context) => {
  const authMode = process.env.D18_AUTH_MODE;
  if (authMode !== "signed_session" && authMode !== "local_static") {
    return undefined;
  }
  return sessionTokenResolver(context, {
    authMode,
    registryPath: process.env.D18_SESSION_REGISTRY_FILE,
    keyPath: process.env.D18_SESSION_SIGNING_KEY_FILE,
    staticToken: process.env.D18_TOKEN,
  });
};

export function sessionCookieFromRequest(request: Request): string | undefined {
  const cookieHeader = request.headers.get("cookie");
  if (!cookieHeader) return undefined;
  for (const part of cookieHeader.split(";")) {
    const [name, ...value] = part.trim().split("=");
    if (name === SESSION_COOKIE_NAME) return value.join("=") || undefined;
  }
  return undefined;
}

export function authContextFromRequest(
  request: Request,
  projectId?: string,
): D18AuthContext {
  return { projectId, sessionId: sessionCookieFromRequest(request) };
}

export function isLoopbackHost(host: string): boolean {
  const normalized = host.trim().toLowerCase();
  return (
    /^(localhost|127\.0\.0\.1)(:\d+)?$/.test(normalized) ||
    /^\[::1\](:\d+)?$/.test(normalized)
  );
}
