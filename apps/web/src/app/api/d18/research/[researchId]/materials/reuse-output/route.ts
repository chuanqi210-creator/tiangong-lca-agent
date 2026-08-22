import { d18Mode, forwardJson, jsonResponse } from "@/lib/d18/proxy";
import { authContextFromRequest, configuredSessionTokenResolver } from "@/server/d18-session";

type Ctx = { params: Promise<{ researchId: string }> };

export async function POST(request: Request, ctx: Ctx) {
  const { researchId } = await ctx.params;
  if (d18Mode() !== "live") return jsonResponse({ code: "material_mutation_requires_live_service" }, 503);
  const key = request.headers.get("Idempotency-Key");
  return forwardJson(
    `/v1/research/${encodeURIComponent(researchId)}/materials/reuse-output`,
    {
      method: "POST",
      body: await request.text(),
      headers: { "Content-Type": "application/json", ...(key ? { "Idempotency-Key": key } : {}) },
      authContext: authContextFromRequest(request, researchId),
    },
    globalThis.fetch,
    configuredSessionTokenResolver,
  );
}
