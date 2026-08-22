import { d18Mode, forwardJson, jsonResponse } from "@/lib/d18/proxy";
import { authContextFromRequest, configuredSessionTokenResolver } from "@/server/d18-session";

type Ctx = { params: Promise<{ uploadId: string }> };

export async function POST(request: Request, ctx: Ctx) {
  const { uploadId } = await ctx.params;
  if (d18Mode() !== "live") return jsonResponse({ code: "uploads_require_live_service" }, 503);
  const projectId = request.headers.get("X-D18-Project-ID") ?? undefined;
  return forwardJson(
    `/v1/material-uploads/${encodeURIComponent(uploadId)}/cancel`,
    {
      method: "POST",
      headers: projectId ? { "X-D18-Project-ID": projectId } : {},
      authContext: authContextFromRequest(request, projectId),
    },
    globalThis.fetch,
    configuredSessionTokenResolver,
  );
}
