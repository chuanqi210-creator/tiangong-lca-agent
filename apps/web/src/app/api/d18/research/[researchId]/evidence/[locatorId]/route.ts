import { d18Mode, forwardJson, jsonResponse } from "@/lib/d18/proxy";
import { authContextFromRequest, configuredSessionTokenResolver } from "@/server/d18-session";

type Ctx = { params: Promise<{ researchId: string; locatorId: string }> };

export async function GET(request: Request, ctx: Ctx) {
  const { researchId, locatorId } = await ctx.params;
  if (d18Mode() !== "live") return jsonResponse({ code: "evidence_unavailable" }, 404);
  return forwardJson(
    `/v1/research/${encodeURIComponent(researchId)}/evidence/${encodeURIComponent(locatorId)}`,
    { method: "GET", authContext: authContextFromRequest(request, researchId) },
    globalThis.fetch,
    configuredSessionTokenResolver,
  );
}
