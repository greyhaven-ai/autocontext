import { createHash } from "node:crypto";

import { z } from "zod";

export const IMAGE_ATTACHMENTS_CAPABILITY = "image_attachments_v1";
export const MAX_IMAGE_ATTACHMENTS = 4;
export const MAX_IMAGE_SOURCE_BYTES = 5 * 1024 * 1024;
export const MAX_IMAGE_DIMENSION = 8_192;
export const MAX_IMAGE_RGBA_BYTES = 64 * 1024 * 1024;
export const MAX_IMAGE_ENCODED_BYTES = 7 * 1024 * 1024;
export const MAX_IMAGE_AGGREGATE_ENCODED_BYTES = 20 * 1024 * 1024;

export const ImageAttachmentSourceSchema = z.enum(["picker", "paste", "drop"]);
export const ImageAttachmentMediaTypeSchema = z.enum([
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
]);

/** Additive protocol-v1 wire shape consumed by chat_agent and inject_hint. */
export const ImageAttachmentSchema = z
  .object({
    id: z.string().trim().min(1).max(200),
    name: z.string().trim().min(1).max(512),
    source: ImageAttachmentSourceSchema,
    media_type: ImageAttachmentMediaTypeSchema,
    data_base64: z.string().min(1).max(MAX_IMAGE_ENCODED_BYTES),
    byte_length: z.number().int().positive().max(MAX_IMAGE_SOURCE_BYTES),
    content_sha256: z.string().regex(/^[0-9a-f]{64}$/),
    width: z.number().int().positive().max(MAX_IMAGE_DIMENSION),
    height: z.number().int().positive().max(MAX_IMAGE_DIMENSION),
  })
  .strict();

export const ImageAttachmentListSchema = z
  .array(ImageAttachmentSchema)
  .max(MAX_IMAGE_ATTACHMENTS);

export type ImageAttachment = z.infer<typeof ImageAttachmentSchema>;
export type ImageAttachmentMediaType = z.infer<typeof ImageAttachmentMediaTypeSchema>;
export type ImageAttachmentSource = z.infer<typeof ImageAttachmentSourceSchema>;

/** Provider-facing form. Only independently verified bytes cross this boundary. */
export interface ValidatedImageAttachment {
  readonly id: string;
  readonly name: string;
  readonly source: ImageAttachmentSource;
  readonly mediaType: ImageAttachmentMediaType;
  readonly byteLength: number;
  readonly contentSha256: string;
  readonly width: number;
  readonly height: number;
  readonly data: Uint8Array;
}

export class ImageAttachmentValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ImageAttachmentValidationError";
  }
}

export function validateImageAttachments(
  input: readonly ImageAttachment[],
): ValidatedImageAttachment[] {
  const attachments = ImageAttachmentListSchema.parse(input);
  const identities = new Set<string>();
  const contentIdentities = new Set<string>();
  let aggregateEncodedBytes = 0;

  return attachments.map((attachment, index) => {
    const label = `image_attachments[${index}]`;
    if (identities.has(attachment.id)) {
      throw new ImageAttachmentValidationError(`${label}.id duplicates '${attachment.id}'`);
    }
    identities.add(attachment.id);
    if (contentIdentities.has(attachment.content_sha256)) {
      throw new ImageAttachmentValidationError(
        `${label}.content_sha256 duplicates another attachment`,
      );
    }
    contentIdentities.add(attachment.content_sha256);

    const encodedBytes = Buffer.byteLength(attachment.data_base64, "ascii");
    aggregateEncodedBytes += encodedBytes;
    if (encodedBytes > MAX_IMAGE_ENCODED_BYTES) {
      throw new ImageAttachmentValidationError(
        `${label}.data_base64 exceeds the 7 MiB encoded limit`,
      );
    }
    if (aggregateEncodedBytes > MAX_IMAGE_AGGREGATE_ENCODED_BYTES) {
      throw new ImageAttachmentValidationError(
        "image_attachments exceed the 20 MiB aggregate encoded limit",
      );
    }
    if (
      attachment.data_base64.length % 4 !== 0 ||
      !hasStrictBase64AlphabetAndPadding(attachment.data_base64)
    ) {
      throw new ImageAttachmentValidationError(`${label}.data_base64 is malformed base64`);
    }

    const bytes = Buffer.from(attachment.data_base64, "base64");
    if (bytes.toString("base64") !== attachment.data_base64) {
      throw new ImageAttachmentValidationError(`${label}.data_base64 is not canonical base64`);
    }
    if (bytes.length !== attachment.byte_length) {
      throw new ImageAttachmentValidationError(
        `${label}.byte_length does not match decoded bytes`,
      );
    }
    if (bytes.length > MAX_IMAGE_SOURCE_BYTES) {
      throw new ImageAttachmentValidationError(`${label} exceeds the 5 MiB source limit`);
    }
    const digest = createHash("sha256").update(bytes).digest("hex");
    if (digest !== attachment.content_sha256) {
      throw new ImageAttachmentValidationError(
        `${label}.content_sha256 does not match decoded bytes`,
      );
    }
    if (attachment.width * attachment.height * 4 > MAX_IMAGE_RGBA_BYTES) {
      throw new ImageAttachmentValidationError(
        `${label} exceeds the 64 MiB decoded RGBA budget`,
      );
    }
    const dimensions = readImageDimensions(bytes, attachment.media_type);
    if (!dimensions) {
      throw new ImageAttachmentValidationError(
        `${label} bytes are not a valid ${attachment.media_type} image`,
      );
    }
    if (dimensions.width !== attachment.width || dimensions.height !== attachment.height) {
      throw new ImageAttachmentValidationError(
        `${label} dimensions do not match the encoded image`,
      );
    }

    return {
      id: attachment.id,
      name: attachment.name,
      source: attachment.source,
      mediaType: attachment.media_type,
      byteLength: attachment.byte_length,
      contentSha256: attachment.content_sha256,
      width: attachment.width,
      height: attachment.height,
      data: new Uint8Array(bytes),
    };
  });
}

/** Linear scan avoids the catastrophic recursion some RegExp engines exhibit on 7 MiB inputs. */
function hasStrictBase64AlphabetAndPadding(value: string): boolean {
  let paddingStart = value.length;
  while (paddingStart > 0 && value.charCodeAt(paddingStart - 1) === 61) paddingStart -= 1;
  const paddingLength = value.length - paddingStart;
  if (paddingLength > 2) return false;
  for (let index = 0; index < paddingStart; index += 1) {
    const code = value.charCodeAt(index);
    const valid =
      (code >= 65 && code <= 90) ||
      (code >= 97 && code <= 122) ||
      (code >= 48 && code <= 57) ||
      code === 43 ||
      code === 47;
    if (!valid) return false;
  }
  return true;
}

function readImageDimensions(
  bytes: Buffer,
  mediaType: ImageAttachmentMediaType,
): { width: number; height: number } | null {
  if (mediaType === "image/png") {
    if (
      bytes.length < 24 ||
      !bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])) ||
      bytes.toString("ascii", 12, 16) !== "IHDR"
    ) return null;
    return dimensions(bytes.readUInt32BE(16), bytes.readUInt32BE(20));
  }
  if (mediaType === "image/gif") {
    if (bytes.length < 10 || !["GIF87a", "GIF89a"].includes(bytes.toString("ascii", 0, 6))) {
      return null;
    }
    return dimensions(bytes.readUInt16LE(6), bytes.readUInt16LE(8));
  }
  if (mediaType === "image/jpeg") return readJpegDimensions(bytes);
  return readWebpDimensions(bytes);
}

function readJpegDimensions(bytes: Buffer): { width: number; height: number } | null {
  if (bytes.length < 4 || bytes[0] !== 0xff || bytes[1] !== 0xd8) return null;
  let offset = 2;
  const sofMarkers = new Set([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf]);
  while (offset + 4 <= bytes.length) {
    if (bytes[offset] !== 0xff) {
      offset += 1;
      continue;
    }
    while (offset < bytes.length && bytes[offset] === 0xff) offset += 1;
    const marker = bytes[offset++];
    if (marker === undefined || marker === 0xd9 || marker === 0xda) return null;
    if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue;
    if (offset + 2 > bytes.length) return null;
    const segmentLength = bytes.readUInt16BE(offset);
    if (segmentLength < 2 || offset + segmentLength > bytes.length) return null;
    if (sofMarkers.has(marker)) {
      if (segmentLength < 7) return null;
      return dimensions(bytes.readUInt16BE(offset + 5), bytes.readUInt16BE(offset + 3));
    }
    offset += segmentLength;
  }
  return null;
}

function readWebpDimensions(bytes: Buffer): { width: number; height: number } | null {
  if (
    bytes.length < 30 ||
    bytes.toString("ascii", 0, 4) !== "RIFF" ||
    bytes.toString("ascii", 8, 12) !== "WEBP"
  ) return null;
  const chunk = bytes.toString("ascii", 12, 16);
  if (chunk === "VP8X") {
    return dimensions(1 + readUInt24LE(bytes, 24), 1 + readUInt24LE(bytes, 27));
  }
  if (chunk === "VP8 ") {
    if (bytes.length < 30 || bytes[23] !== 0x9d || bytes[24] !== 0x01 || bytes[25] !== 0x2a) {
      return null;
    }
    return dimensions(bytes.readUInt16LE(26) & 0x3fff, bytes.readUInt16LE(28) & 0x3fff);
  }
  if (chunk === "VP8L") {
    if (bytes.length < 25 || bytes[20] !== 0x2f) return null;
    const bits = bytes.readUInt32LE(21);
    return dimensions((bits & 0x3fff) + 1, ((bits >>> 14) & 0x3fff) + 1);
  }
  return null;
}

function readUInt24LE(bytes: Buffer, offset: number): number {
  return bytes[offset]! | (bytes[offset + 1]! << 8) | (bytes[offset + 2]! << 16);
}

function dimensions(width: number, height: number): { width: number; height: number } | null {
  return width > 0 && height > 0 ? { width, height } : null;
}

export function encodeValidatedImage(attachment: ValidatedImageAttachment): string {
  return Buffer.from(attachment.data).toString("base64");
}
