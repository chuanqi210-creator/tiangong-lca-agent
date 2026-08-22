import { fxEvents } from "@/lib/d18/fixture-engine";
import { d18Mode, forwardStream, jsonResponse } from "@/lib/d18/proxy";
import {
  authContextFromRequest,
  configuredSessionTokenResolver,
} from "@/server/d18-session";

type Ctx = { params: Promise<{ id: string }> };

/** GET /api/d18/jobs/{id}/events —— SSE 透传，转发 Last-Event-ID 游标 */
export async function GET(request: Request, ctx: Ctx) {
  const { id } = await ctx.params;

  if (d18Mode() === "live") {
    const url = new URL(request.url);
    const after = url.searchParams.get("after");
    const lastEventId = request.headers.get("Last-Event-ID");
    return forwardStream(
      `/v1/jobs/${encodeURIComponent(id)}/events${after ? `?after=${encodeURIComponent(after)}` : ""}`,
      {
        method: "GET",
        headers: lastEventId ? { "Last-Event-ID": lastEventId } : {},
        authContext: authContextFromRequest(request),
      },
      globalThis.fetch,
      configuredSessionTokenResolver,
    );
  }

  // —— fixture 回放：帧格式对齐 events.py ——
  const url = new URL(request.url);
  let after = Number(url.searchParams.get("after") ?? 0) || 0;
  const lastEventId = request.headers.get("Last-Event-ID");
  if (lastEventId) {
    const n = Number(lastEventId);
    if (Number.isInteger(n)) after = Math.max(after, n);
  }
  const result = fxEvents(id, after);
  if (result.status !== 200) return jsonResponse(result.body, result.status);

  const chunks: string[] = [];
  for (const evt of result.frames!) {
    chunks.push(`id: ${evt.sequence}\nevent: job\ndata: ${JSON.stringify(evt)}\n\n`);
  }
  chunks.push(
    `event: heartbeat\ndata: ${JSON.stringify({ cursor: result.cursor, status: "connected" })}\n\n`,
  );
  chunks.push("data: [DONE]\n\n");

  return new Response(chunks.join(""), {
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache",
    },
  });
}
