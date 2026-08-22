import { d18Mode, forwardJson, jsonResponse } from "@/lib/d18/proxy";
import { authContextFromRequest, configuredSessionTokenResolver } from "@/server/d18-session";

type Ctx = { params: Promise<{ researchId: string }> };

export async function GET(request: Request, ctx: Ctx) {
  const { researchId } = await ctx.params;
  if (d18Mode() !== "live") return jsonResponse({ materials: [] });
  return forwardJson(
    `/v1/research/${encodeURIComponent(researchId)}/materials${new URL(request.url).search}`,
    { method: "GET", authContext: authContextFromRequest(request, researchId) },
    globalThis.fetch,
    configuredSessionTokenResolver,
  );
}
