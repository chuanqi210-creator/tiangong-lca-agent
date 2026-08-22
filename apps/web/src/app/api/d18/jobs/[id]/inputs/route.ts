import { fxInput } from "@/lib/d18/fixture-engine";
import { d18Mode, forwardJson, jsonResponse } from "@/lib/d18/proxy";
import {
  authContextFromRequest,
  configuredSessionTokenResolver,
} from "@/server/d18-session";

type Ctx = { params: Promise<{ id: string }> };

/** POST /api/d18/jobs/{id}/inputs —— 补充材料 / 决定 */
export async function POST(request: Request, ctx: Ctx) {
  const { id } = await ctx.params;
  const bodyText = await request.text();

  if (d18Mode() === "live") {
    return forwardJson(
      `/v1/jobs/${encodeURIComponent(id)}/inputs`,
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
  let kind = "";
  try {
    const parsed = JSON.parse(bodyText);
    inputId = String(parsed.input_id ?? "");
    kind = String(parsed.kind ?? "");
  } catch {
    return jsonResponse({ detail: "invalid JSON body" }, 422);
  }
  if (!inputId || (kind !== "material" && kind !== "decision")) {
    return jsonResponse({ detail: "input_id and kind are required" }, 422);
  }
  const { status, body } = fxInput(id, inputId, kind);
  return jsonResponse(body, status);
}
