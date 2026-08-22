import type { D18Attachment } from "./types";

/** Keep the bearer token server-side by routing registered backend files through the BFF. */
export function mapRegisteredAttachmentUrls(value: unknown): unknown {
  if (!value || typeof value !== "object" || Array.isArray(value)) return value;
  const body = value as Record<string, unknown>;
  if (!Array.isArray(body.attachments)) return value;
  return {
    ...body,
    attachments: body.attachments.map((candidate) => {
      if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) {
        throw new Error("invalid registered attachment");
      }
      const attachment = candidate as D18Attachment;
      const match = /^\/v1\/files\/([A-Za-z0-9_-]+)$/.exec(attachment.fileUrl);
      if (!match || attachment.url !== attachment.fileUrl) {
        throw new Error("attachment URL is outside the registered backend route");
      }
      const publicUrl = `/api/d18/files/${encodeURIComponent(match[1])}`;
      return { ...attachment, url: publicUrl, fileUrl: publicUrl };
    }),
  };
}
