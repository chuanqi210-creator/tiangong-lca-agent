import { fxCreateJob } from "@/lib/d18/fixture-engine";
import { d18Mode, forwardJson, jsonResponse } from "@/lib/d18/proxy";
import {
  authContextFromRequest,
  configuredSessionTokenResolver,
} from "@/server/d18-session";

/** POST /api/d18/jobs —— 创建异步任务；Idempotency-Key 透传 */
export async function POST(request: Request) {
  const bodyText = await request.text();
  const idempotencyKey = request.headers.get("Idempotency-Key");
  let researchId = "";
  try {
    researchId = String(JSON.parse(bodyText).research_id ?? "");
  } catch {
    return jsonResponse({ detail: "invalid JSON body" }, 422);
  }
  if (!researchId) return jsonResponse({ detail: "research_id is required" }, 422);

  if (d18Mode() === "live") {
    return forwardJson(
      "/v1/jobs",
      {
        method: "POST",
        body: bodyText,
        headers: {
          "Content-Type": "application/json",
          ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
        },
        authContext: authContextFromRequest(request, researchId),
      },
      globalThis.fetch,
      configuredSessionTokenResolver,
    );
  }

  // —— fixture 回放 ——
  if (!idempotencyKey) {
    return jsonResponse({ detail: "Idempotency-Key is required" }, 422);
  }
  const { status, body } = fxCreateJob(researchId, idempotencyKey);
  return jsonResponse(body, status);
}
