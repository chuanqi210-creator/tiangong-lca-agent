import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import type { UIMessage } from "../src/components/chat/types.ts";

test("assistant messages require an explicit display provenance", () => {
  const message: UIMessage = {
    id: "assistant-1",
    kind: "assistant",
    text: "本地界面提示",
    streaming: false,
    provenance: "local_ui_notice",
  };

  assert.equal(message.provenance, "local_ui_notice");
});

test("job poller cannot fabricate a backend acknowledgement for free text", () => {
  const source = readFileSync(
    new URL("../src/lib/use-job-poller.ts", import.meta.url),
    "utf8",
  );

  assert.doesNotMatch(source, /appendAssistant\(copy\.research\.midRunNote\)/);
  assert.doesNotMatch(source, /已收到你的补充说明/);
  assert.match(source, /followupUnavailable/);
});

test("research shell disables active free text but enables waiting and terminal turns", () => {
  const shell = readFileSync(
    new URL("../src/components/research/ResearchShell.tsx", import.meta.url),
    "utf8",
  );
  const composer = readFileSync(
    new URL("../src/components/chat/Composer.tsx", import.meta.url),
    "utf8",
  );

  assert.match(shell, /inputDisabled=\{active\}/);
  assert.match(shell, /continuationPlaceholder/);
  assert.match(composer, /inputDisabled: boolean/);
  assert.match(
    composer,
    /PromptInputTextarea[\s\S]*?disabled=\{disabled \|\| inputDisabled\}/,
  );
  assert.match(composer, /SubmitButton busy=\{disabled\}/);
});

test("terminal follow-up is shown only after a backend continuation receipt", () => {
  const source = readFileSync(
    new URL("../src/lib/use-job-poller.ts", import.meta.url),
    "utf8",
  );

  const receipt = source.indexOf("await createContinuation(");
  const userEcho = source.indexOf("appendUser(text);", receipt);
  const assistantReceipt = source.indexOf(
    '"backend_input_receipt"',
    receipt,
  );
  assert.ok(receipt >= 0);
  assert.ok(userEcho > receipt);
  assert.ok(assistantReceipt > receipt);
});

test("waiting free text continues the research turn instead of fabricating a material file", () => {
  const shell = readFileSync(
    new URL("../src/components/research/ResearchShell.tsx", import.meta.url),
    "utf8",
  );

  assert.doesNotMatch(shell, /supplement-\$\{entry\?\.inputSeq/);
  assert.doesNotMatch(shell, /buildSourceMaterials\(\[/);
  assert.match(shell, /onGapAnswer=\{submitUserTurn\}/);
  assert.match(shell, /submitObjective\(text\)/);
});
