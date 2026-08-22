import { fxGetJob } from "@/lib/d18/fixture-engine";
import { d18Mode, forwardJson, jsonResponse } from "@/lib/d18/proxy";
import {
  authContextFromRequest,
  configuredSessionTokenResolver,
} from "@/server/d18-session";

type Ctx = { params: Promise<{ id: string }> };

/** GET /api/d18/jobs/{id} —— 任务状态查询 */
export async function GET(request: Request, ctx: Ctx) {
  const { id } = await ctx.params;
  if (d18Mode() === "live") {
    return forwardJson(
      `/v1/jobs/${encodeURIComponent(id)}`,
      { method: "GET", authContext: authContextFromRequest(request) },
      globalThis.fetch,
      configuredSessionTokenResolver,
    );
  }
  const { status, body } = fxGetJob(id);
  return jsonResponse(body, status);
}
