import { d18Mode, forwardJson, jsonResponse } from "@/lib/d18/proxy";
import { authContextFromRequest, configuredSessionTokenResolver } from "@/server/d18-session";

type Ctx = { params: Promise<{ researchId: string; materialId: string }> };

export async function GET(request: Request, ctx: Ctx) {
  const { researchId, materialId } = await ctx.params;
  if (d18Mode() !== "live") return jsonResponse({ code: "material_not_found" }, 404);
  return forwardJson(
    `/v1/research/${encodeURIComponent(researchId)}/materials/${encodeURIComponent(materialId)}`,
    { method: "GET", authContext: authContextFromRequest(request, researchId) },
    globalThis.fetch,
    configuredSessionTokenResolver,
  );
}

export async function DELETE(request: Request, ctx: Ctx) {
  const { researchId, materialId } = await ctx.params;
  if (d18Mode() !== "live") return jsonResponse({ code: "material_not_found" }, 404);
  const idempotencyKey = request.headers.get("Idempotency-Key");
  return forwardJson(
    `/v1/research/${encodeURIComponent(researchId)}/materials/${encodeURIComponent(materialId)}`,
    {
      method: "DELETE",
      headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
      authContext: authContextFromRequest(request, researchId),
    },
    globalThis.fetch,
    configuredSessionTokenResolver,
  );
}
