import {
  fxAttachments,
  fxProjection,
  fxProjectRecordRef,
} from "@/lib/d18/fixture-engine";
import { d18Mode, forwardJson, forwardStream, jsonResponse } from "@/lib/d18/proxy";
import {
  authContextFromRequest,
  configuredSessionTokenResolver,
} from "@/server/d18-session";

const enc = new TextEncoder();

/** POST /api/d18/chat/completions —— OpenAI 兼容 + research_id；SSE 透传 */
export async function POST(request: Request) {
  const bodyText = await request.text();
  let parsed: Record<string, unknown> | undefined;
  try {
    parsed = JSON.parse(bodyText) as Record<string, unknown>;
  } catch {
    /* fixture returns 422 below; live lets the upstream classify invalid JSON */
  }

  if (d18Mode() === "live") {
    const stream = parsed?.stream === true;
    const projectId =
      typeof parsed?.research_id === "string" ? parsed.research_id : undefined;
    const init = {
      method: "POST",
      body: bodyText,
      headers: { "Content-Type": "application/json" },
      authContext: authContextFromRequest(request, projectId),
    };
    return stream
      ? forwardStream(
          "/v1/chat/completions",
          init,
          globalThis.fetch,
          configuredSessionTokenResolver,
        )
      : forwardJson(
          "/v1/chat/completions",
          init,
          globalThis.fetch,
          configuredSessionTokenResolver,
        );
  }

  // —— fixture 回放 ——
  let researchId = "fixture-sync";
  let stream = false;
  if (parsed) {
    if (typeof parsed.research_id === "string") researchId = parsed.research_id;
    stream = parsed.stream === true;
  } else {
    return jsonResponse({ detail: "invalid JSON body" }, 422);
  }

  const projection = fxProjection(researchId);
  const content = JSON.stringify(projection);
  const recordRef = fxProjectRecordRef(researchId);
  const usage = { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 };

  if (!stream) {
    return jsonResponse({
      id: "chatcmpl-fixture",
      object: "chat.completion",
      created: 0,
      model: "d18-research-director",
      choices: [
        {
          index: 0,
          message: { role: "assistant", content },
          finish_reason: "stop",
        },
      ],
      usage,
      x_d18: { projectRecordRef: recordRef },
    });
  }

  const base = {
    id: "chatcmpl-fixture",
    object: "chat.completion.chunk",
    created: 0,
    model: "d18-research-director",
  };
  const frames: string[] = [];
  const frame = (obj: unknown) => `data: ${JSON.stringify(obj)}\n\n`;
  frames.push(
    frame({
      ...base,
      choices: [{ index: 0, delta: { role: "assistant" }, finish_reason: null }],
    }),
  );
  frames.push(
    frame({
      ...base,
      choices: [{ index: 0, delta: { content }, finish_reason: null }],
    }),
  );
  frames.push(
    frame({
      ...base,
      choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
      usage,
      x_soda: { attachments: fxAttachments() },
      x_d18: { projectRecordRef: recordRef },
    }),
  );
  frames.push("data: [DONE]\n\n");

  return new Response(enc.encode(frames.join("")), {
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
    },
  });
}
