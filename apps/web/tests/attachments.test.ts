import assert from "node:assert/strict";
import test from "node:test";

import { mapRegisteredAttachmentUrls } from "../src/lib/d18/attachments.ts";

test("registered backend attachments are translated to the browser BFF", () => {
  const mapped = mapRegisteredAttachmentUrls({
    attachments: [
      {
        id: "abc",
        url: "/v1/files/abc",
        fileUrl: "/v1/files/abc",
        fileName: "result.csv",
        fileType: "data",
        mimeType: "text/csv",
      },
    ],
  }) as { attachments: Array<{ fileUrl: string; url: string }> };
  assert.equal(mapped.attachments[0].fileUrl, "/api/d18/files/abc");
  assert.equal(mapped.attachments[0].url, "/api/d18/files/abc");
});

test("unregistered or absolute attachment URLs fail closed", () => {
  assert.throws(
    () => mapRegisteredAttachmentUrls({
      attachments: [{ url: "https://evil.invalid/a", fileUrl: "https://evil.invalid/a" }],
    }),
    /outside the registered backend route/,
  );
});
