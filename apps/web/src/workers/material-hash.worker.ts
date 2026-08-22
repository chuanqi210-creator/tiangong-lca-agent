/// <reference lib="webworker" />

import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex } from "@noble/hashes/utils.js";

let wholeFile = sha256.create();
let parts: Array<{ partNumber: number; sha256: string }> = [];

self.addEventListener(
  "message",
  (event: MessageEvent<
    | { type: "start" }
    | { type: "chunk"; partNumber: number; bytes: ArrayBuffer }
    | { type: "finish" }
  >) => {
    try {
      if (event.data.type === "start") {
        wholeFile = sha256.create();
        parts = [];
        self.postMessage({ type: "ready" });
        return;
      }
      if (event.data.type === "chunk") {
        const chunk = new Uint8Array(event.data.bytes);
        wholeFile.update(chunk);
        const part = {
          partNumber: event.data.partNumber,
          sha256: bytesToHex(sha256(chunk)),
        };
        parts.push(part);
        self.postMessage({ type: "chunk_complete", part });
        return;
      }
      if (event.data.type === "finish") {
        self.postMessage({
          type: "complete",
          sha256: bytesToHex(wholeFile.digest()),
          parts,
        });
      }
    } catch {
      self.postMessage({ type: "error" });
    }
  },
);

export {};
