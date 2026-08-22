import { d18Mode, forwardRequestStream, jsonResponse } from "@/lib/d18/proxy";
import {
  authContextFromRequest,
  configuredSessionTokenResolver,
} from "@/server/d18-session";

type Ctx = { params: Promise<{ uploadId: string; partNumber: string }> };

export async function PUT(request: Request, ctx: Ctx) {
  const { uploadId, partNumber } = await ctx.params;
  if (d18Mode() !== "live") return jsonResponse({ code: "uploads_require_live_service" }, 503);
  const projectId = request.headers.get("X-D18-Project-ID") ?? undefined;
  return forwardRequestStream(
    `/v1/material-uploads/${encodeURIComponent(uploadId)}/parts/${encodeURIComponent(partNumber)}`,
    request,
    { method: "PUT", authContext: authContextFromRequest(request, projectId) },
    globalThis.fetch,
    configuredSessionTokenResolver,
  );
}
