import { fxContinuation } from "@/lib/d18/fixture-engine";
import { d18Mode, forwardJson, jsonResponse } from "@/lib/d18/proxy";
import {
  authContextFromRequest,
  configuredSessionTokenResolver,
} from "@/server/d18-session";

type Ctx = { params: Promise<{ id: string }> };

/** POST /api/d18/jobs/{id}/continuations —— 终态后的同项目续问 */
export async function POST(request: Request, ctx: Ctx) {
  const { id } = await ctx.params;
  const bodyText = await request.text();

  if (d18Mode() === "live") {
    return forwardJson(
      `/v1/jobs/${encodeURIComponent(id)}/continuations`,
      {
        method: "POST",
        body: bodyText,
        headers: { "Content-Type": "application/json" },
        authContext: authContextFromRequest(request),
      },
      globalThis.fetch,
      configuredSessionTokenResolver,
    );
  }

  let inputId = "";
  let text = "";
  try {
    const parsed = JSON.parse(bodyText);
    inputId = String(parsed.input_id ?? "");
    text = String(parsed.text ?? "");
  } catch {
    return jsonResponse({ detail: "invalid JSON body" }, 422);
  }
  if (
    !inputId.trim() ||
    inputId.length > 255 ||
    !text.trim() ||
    text.length > 32_768
  ) {
    return jsonResponse({ detail: "input_id and text are required" }, 422);
  }
  const { status, body } = fxContinuation(id, inputId, text);
  return jsonResponse(body, status);
}
