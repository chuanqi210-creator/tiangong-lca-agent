import { fxResult } from "@/lib/d18/fixture-engine";
import { mapRegisteredAttachmentUrls } from "@/lib/d18/attachments";
import { d18Mode, forwardJson, jsonResponse } from "@/lib/d18/proxy";
import {
  authContextFromRequest,
  configuredSessionTokenResolver,
} from "@/server/d18-session";

type Ctx = { params: Promise<{ id: string }> };

/** GET /api/d18/jobs/{id}/result —— 研究结果视图 */
export async function GET(request: Request, ctx: Ctx) {
  const { id } = await ctx.params;
  if (d18Mode() === "live") {
    const upstream = await forwardJson(
      `/v1/jobs/${encodeURIComponent(id)}/result`,
      { method: "GET", authContext: authContextFromRequest(request) },
      globalThis.fetch,
      configuredSessionTokenResolver,
    );
    if (!upstream.ok) return upstream;
    try {
      return jsonResponse(mapRegisteredAttachmentUrls(await upstream.json()));
    } catch {
      return jsonResponse(
        { code: "invalid_upstream_result", detail: "后端结果附件无效。" },
        502,
      );
    }
  }
  const { status, body } = fxResult(id);
  return jsonResponse(body, status);
}
