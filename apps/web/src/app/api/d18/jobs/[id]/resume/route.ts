import { fxResume } from "@/lib/d18/fixture-engine";
import { d18Mode, forwardJson, jsonResponse } from "@/lib/d18/proxy";
import {
  authContextFromRequest,
  configuredSessionTokenResolver,
} from "@/server/d18-session";

type Ctx = { params: Promise<{ id: string }> };

/** POST /api/d18/jobs/{id}/resume —— 合同语义：仅 waiting_for_input 可恢复 */
export async function POST(request: Request, ctx: Ctx) {
  const { id } = await ctx.params;
  if (d18Mode() === "live") {
    return forwardJson(
      `/v1/jobs/${encodeURIComponent(id)}/resume`,
      { method: "POST", authContext: authContextFromRequest(request) },
      globalThis.fetch,
      configuredSessionTokenResolver,
    );
  }
  const { status, body } = fxResume(id);
  return jsonResponse(body, status);
}
