import { d18Mode, forwardStream, jsonResponse } from "@/lib/d18/proxy";
import { authContextFromRequest, configuredSessionTokenResolver } from "@/server/d18-session";

type Ctx = { params: Promise<{ researchId: string; materialId: string }> };

export async function GET(request: Request, ctx: Ctx) {
  const { researchId, materialId } = await ctx.params;
  if (d18Mode() !== "live") return jsonResponse({ code: "material_unavailable" }, 404);
  return forwardStream(
    `/v1/research/${encodeURIComponent(researchId)}/materials/${encodeURIComponent(materialId)}/download`,
    { method: "GET", authContext: authContextFromRequest(request, researchId) },
    globalThis.fetch,
    configuredSessionTokenResolver,
  );
}
