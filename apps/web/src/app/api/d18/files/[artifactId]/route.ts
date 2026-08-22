import { fxFile } from "@/lib/d18/fixture-engine";
import { d18Mode, forwardStream, jsonResponse } from "@/lib/d18/proxy";
import {
  authContextFromRequest,
  configuredSessionTokenResolver,
} from "@/server/d18-session";

type Ctx = { params: Promise<{ artifactId: string }> };

/** GET /api/d18/files/{artifactId} —— 附件下载转发（token 不出服务端） */
export async function GET(request: Request, ctx: Ctx) {
  const { artifactId } = await ctx.params;

  if (d18Mode() === "live") {
    return forwardStream(
      `/v1/files/${encodeURIComponent(artifactId)}`,
      { method: "GET", authContext: authContextFromRequest(request) },
      globalThis.fetch,
      configuredSessionTokenResolver,
    );
  }

  const file = fxFile(artifactId);
  if (file.status !== 200) {
    return jsonResponse({ detail: "artifact unavailable" }, 404);
  }
  return new Response(new Uint8Array(file.bytes), {
    headers: {
      "Content-Type": file.mimeType,
      "Content-Disposition": `attachment; filename*=UTF-8''${encodeURIComponent(file.fileName)}`,
      "Cache-Control": "no-store",
    },
  });
}
