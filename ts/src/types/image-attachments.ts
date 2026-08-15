import { createHash } from "node:crypto";
import { inflateSync } from "node:zlib";

import { z } from "zod";

export const IMAGE_ATTACHMENTS_CAPABILITY = "image_attachments_v1";
export const MAX_IMAGE_ATTACHMENTS = 4;
export const MAX_IMAGE_SOURCE_BYTES = 5 * 1024 * 1024;
export const MAX_IMAGE_DIMENSION = 8_192;
export const MAX_IMAGE_RGBA_BYTES = 64 * 1024 * 1024;
export const MAX_IMAGE_ENCODED_BYTES = 7 * 1024 * 1024;
export const MAX_IMAGE_AGGREGATE_ENCODED_BYTES = 20 * 1024 * 1024;

const PNG_CRC_TABLE = Uint32Array.from({ length: 256 }, (_, value) => {
  let crc = value;
  for (let bit = 0; bit < 8; bit += 1) {
    crc = (crc >>> 1) ^ ((crc & 1) === 1 ? 0xedb88320 : 0);
  }
  return crc >>> 0;
});
const MAX_IMAGE_DECODE_PIXELS = Math.floor(MAX_IMAGE_RGBA_BYTES / 4);
// PNG scanlines carry one filter byte per pass row in addition to pixel bytes.
// Keep that bounded structural overhead separate from the decoded RGBA budget.
const MAX_PNG_FILTER_OVERHEAD_BYTES = MAX_IMAGE_DIMENSION * 2;
const MAX_PENDING_IMAGE_DECODE_REQUESTS = 4;
let imageDecodeTail: Promise<void> = Promise.resolve();
let pendingImageDecodes = 0;

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

/** Provider-facing form. Inference paths must additionally call the async decode validator. */
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
    const dimensions = validateImageStructure(bytes, attachment.media_type);
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

/**
 * Fully decodes structurally validated images before they cross an inference boundary.
 * Decodes are serialized process-wide so concurrent commands cannot each allocate the
 * full 64 MiB pixel budget at once.
 */
export async function validateImageAttachmentsForInference(
  input: readonly ImageAttachment[],
): Promise<ValidatedImageAttachment[]> {
  const attachments = validateImageAttachments(input);
  for (let index = 0; index < attachments.length; index += 1) {
    await withImageDecodeSlot(() => fullyDecodeImage(attachments[index]!, index));
  }
  return attachments;
}

function withImageDecodeSlot<T>(task: () => Promise<T>): Promise<T> {
  if (pendingImageDecodes >= MAX_PENDING_IMAGE_DECODE_REQUESTS) {
    return Promise.reject(new ImageAttachmentValidationError(
      "The image decode queue is full; retry after current image validation completes",
    ));
  }
  pendingImageDecodes += 1;
  const scheduled = imageDecodeTail.then(task, task);
  imageDecodeTail = scheduled.then(() => undefined, () => undefined);
  return scheduled.then(
    (value) => {
      pendingImageDecodes -= 1;
      return value;
    },
    (error: unknown) => {
      pendingImageDecodes -= 1;
      throw error;
    },
  );
}

async function fullyDecodeImage(
  attachment: ValidatedImageAttachment,
  index: number,
): Promise<void> {
  const label = `image_attachments[${index}]`;
  const input = Buffer.from(attachment.data);
  const expectedFormat: Record<ImageAttachmentMediaType, string> = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
  };
  const decodeOptions = {
    animated: true,
    failOn: "warning" as const,
    limitInputPixels: MAX_IMAGE_DECODE_PIXELS,
    sequentialRead: true,
  };

  try {
    const { default: sharp } = await import("sharp");
    const metadata = await sharp(input, decodeOptions).metadata();
    if (
      metadata.format !== expectedFormat[attachment.mediaType] ||
      metadata.width !== attachment.width ||
      metadata.height !== attachment.height ||
      (metadata.pages ?? 1) !== 1 ||
      (metadata.channels !== undefined && (metadata.channels < 1 || metadata.channels > 4))
    ) {
      throw new ImageAttachmentValidationError(
        `${label} decoded metadata does not match the validated single-frame image`,
      );
    }
    if (metadata.width * metadata.height > MAX_IMAGE_DECODE_PIXELS) {
      throw new ImageAttachmentValidationError(`${label} exceeds the decoded pixel budget`);
    }

    const decoded = await sharp(input, decodeOptions)
      .raw()
      .toBuffer({ resolveWithObject: true });
    const decodedBytes = decoded.data.byteLength;
    const decodedMatches =
      decoded.info.width === attachment.width &&
      decoded.info.height === attachment.height &&
      decoded.info.channels >= 1 &&
      decoded.info.channels <= 4 &&
      decodedBytes === decoded.info.width * decoded.info.height * decoded.info.channels &&
      decodedBytes <= MAX_IMAGE_RGBA_BYTES;
    decoded.data.fill(0);
    if (!decodedMatches) {
      throw new ImageAttachmentValidationError(
        `${label} decoded pixels exceed the supported dimensions or channel budget`,
      );
    }
  } catch (error) {
    if (error instanceof ImageAttachmentValidationError) throw error;
    throw new ImageAttachmentValidationError(
      `${label} could not be fully decoded as ${attachment.mediaType}`,
    );
  }
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

function validateImageStructure(
  bytes: Buffer,
  mediaType: ImageAttachmentMediaType,
): { width: number; height: number } | null {
  if (mediaType === "image/png") return validatePng(bytes);
  if (mediaType === "image/gif") return validateGif(bytes);
  if (mediaType === "image/jpeg") return validateJpeg(bytes);
  return validateWebp(bytes);
}

function validatePng(bytes: Buffer): { width: number; height: number } | null {
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  if (bytes.length < 45 || !bytes.subarray(0, 8).equals(signature)) return null;

  let offset = 8;
  let chunkIndex = 0;
  let width = 0;
  let height = 0;
  let bitsPerPixel = 0;
  let interlace = 0;
  let colorType = -1;
  let paletteEntries = 0;
  let sawPlte = false;
  let sawIdat = false;
  let idatEnded = false;
  let sawIend = false;
  const idatChunks: Buffer[] = [];

  while (offset + 12 <= bytes.length) {
    const chunkLength = bytes.readUInt32BE(offset);
    const dataStart = offset + 8;
    const dataEnd = dataStart + chunkLength;
    const crcEnd = dataEnd + 4;
    if (dataEnd < dataStart || crcEnd > bytes.length) return null;
    const type = bytes.toString("ascii", offset + 4, offset + 8);
    if (!/^[A-Za-z]{4}$/.test(type)) return null;
    const expectedCrc = bytes.readUInt32BE(dataEnd);
    if (pngCrc32(bytes.subarray(offset + 4, dataEnd)) !== expectedCrc) return null;
    if (chunkIndex === 0 && type !== "IHDR") return null;

    if (type === "IHDR") {
      if (chunkIndex !== 0 || chunkLength !== 13) return null;
      width = bytes.readUInt32BE(dataStart);
      height = bytes.readUInt32BE(dataStart + 4);
      const bitDepth = bytes[dataStart + 8]!;
      colorType = bytes[dataStart + 9]!;
      const channels = new Map<number, number>([
        [0, 1],
        [2, 3],
        [3, 1],
        [4, 2],
        [6, 4],
      ]).get(colorType);
      const validDepths: Record<number, readonly number[]> = {
        0: [1, 2, 4, 8, 16],
        2: [8, 16],
        3: [1, 2, 4, 8],
        4: [8, 16],
        6: [8, 16],
      };
      if (
        !dimensions(width, height) ||
        channels === undefined ||
        !validDepths[colorType]!.includes(bitDepth) ||
        bytes[dataStart + 10] !== 0 ||
        bytes[dataStart + 11] !== 0 ||
        ![0, 1].includes(bytes[dataStart + 12]!)
      ) return null;
      bitsPerPixel = channels * bitDepth;
      interlace = bytes[dataStart + 12]!;
    } else if (type === "PLTE") {
      if (sawPlte || sawIdat || chunkLength === 0 || chunkLength % 3 !== 0 || chunkLength > 768) return null;
      sawPlte = true;
      paletteEntries = chunkLength / 3;
      if (colorType === 0 || colorType === 4) return null;
    } else if (type === "IDAT") {
      if (idatEnded || (colorType === 3 && paletteEntries === 0)) return null;
      sawIdat = true;
      idatChunks.push(bytes.subarray(dataStart, dataEnd));
    } else if (type === "IEND") {
      if (chunkLength !== 0 || !sawIdat || crcEnd !== bytes.length) return null;
      sawIend = true;
    } else {
      if (["acTL", "fcTL", "fdAT"].includes(type)) return null;
      if ((type.charCodeAt(0) & 0x20) === 0) return null;
      if (sawIdat) idatEnded = true;
    }

    offset = crcEnd;
    chunkIndex += 1;
    if (sawIend) break;
  }

  if (!sawIend || offset !== bytes.length) return null;
  if (colorType === 3 && paletteEntries > 2 ** bitsPerPixel) return null;
  const passes = pngPasses(width, height, bitsPerPixel, interlace);
  const expectedInflatedBytes = passes.reduce(
    (total, pass) => total + (pass.rowBytes + 1) * pass.height,
    0,
  );
  if (
    expectedInflatedBytes <= 0 ||
    expectedInflatedBytes > MAX_IMAGE_RGBA_BYTES + MAX_PNG_FILTER_OVERHEAD_BYTES
  ) return null;

  let inflated: Buffer;
  try {
    inflated = inflateSync(Buffer.concat(idatChunks), {
      maxOutputLength: expectedInflatedBytes,
    });
  } catch {
    return null;
  }
  if (inflated.length !== expectedInflatedBytes) return null;
  let rowOffset = 0;
  for (const pass of passes) {
    for (let row = 0; row < pass.height; row += 1) {
      if (inflated[rowOffset] === undefined || inflated[rowOffset]! > 4) return null;
      rowOffset += pass.rowBytes + 1;
    }
  }
  return dimensions(width, height);
}

interface PngPass {
  rowBytes: number;
  height: number;
}

function pngPasses(
  width: number,
  height: number,
  bitsPerPixel: number,
  interlace: number,
): PngPass[] {
  if (interlace === 0) {
    return [{ rowBytes: Math.ceil(width * bitsPerPixel / 8), height }];
  }
  const adam7 = [
    [0, 0, 8, 8],
    [4, 0, 8, 8],
    [0, 4, 4, 8],
    [2, 0, 4, 4],
    [0, 2, 2, 4],
    [1, 0, 2, 2],
    [0, 1, 1, 2],
  ] as const;
  return adam7.flatMap(([startX, startY, stepX, stepY]) => {
    const passWidth = width <= startX ? 0 : Math.ceil((width - startX) / stepX);
    const passHeight = height <= startY ? 0 : Math.ceil((height - startY) / stepY);
    return passWidth === 0 || passHeight === 0
      ? []
      : [{ rowBytes: Math.ceil(passWidth * bitsPerPixel / 8), height: passHeight }];
  });
}

function pngCrc32(bytes: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc = (crc >>> 8) ^ PNG_CRC_TABLE[(crc ^ byte) & 0xff]!;
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function validateJpeg(bytes: Buffer): { width: number; height: number } | null {
  if (bytes.length < 4 || bytes[0] !== 0xff || bytes[1] !== 0xd8) return null;
  let offset = 2;
  let imageDimensions: { width: number; height: number } | null = null;
  let sawScan = false;
  const sofMarkers = new Set([
    0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7,
    0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf,
  ]);
  while (offset < bytes.length) {
    if (bytes[offset] !== 0xff) return null;
    while (offset < bytes.length && bytes[offset] === 0xff) offset += 1;
    const marker = bytes[offset++];
    if (marker === undefined || marker === 0x00 || marker === 0xd8) return null;
    if (marker === 0xd9) {
      return sawScan && imageDimensions && offset === bytes.length ? imageDimensions : null;
    }
    if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) return null;
    if (offset + 2 > bytes.length) return null;
    const segmentLength = bytes.readUInt16BE(offset);
    if (segmentLength < 2 || offset + segmentLength > bytes.length) return null;
    if (sofMarkers.has(marker)) {
      if (segmentLength < 11 || bytes[offset + 2] === 0) return null;
      const componentCount = bytes[offset + 7]!;
      if (componentCount === 0 || segmentLength !== 8 + componentCount * 3) return null;
      const parsed = dimensions(bytes.readUInt16BE(offset + 5), bytes.readUInt16BE(offset + 3));
      if (!parsed || (imageDimensions && (
        parsed.width !== imageDimensions.width || parsed.height !== imageDimensions.height
      ))) return null;
      imageDimensions = parsed;
    }
    if (marker !== 0xda) {
      offset += segmentLength;
      continue;
    }
    const scanComponents = bytes[offset + 2]!;
    if (scanComponents === 0 || segmentLength !== 6 + scanComponents * 2 || !imageDimensions) {
      return null;
    }
    sawScan = true;
    offset += segmentLength;
    let foundMarker = false;
    let sawEntropyData = false;
    while (offset < bytes.length) {
      if (bytes[offset] !== 0xff) {
        sawEntropyData = true;
        offset += 1;
        continue;
      }
      const markerStart = offset;
      while (offset < bytes.length && bytes[offset] === 0xff) offset += 1;
      const entropyMarker = bytes[offset];
      if (entropyMarker === 0x00 || (entropyMarker !== undefined && entropyMarker >= 0xd0 && entropyMarker <= 0xd7)) {
        if (entropyMarker === 0x00) sawEntropyData = true;
        offset += 1;
        continue;
      }
      offset = markerStart;
      foundMarker = true;
      break;
    }
    if (!foundMarker || !sawEntropyData) return null;
  }
  return null;
}

function validateGif(bytes: Buffer): { width: number; height: number } | null {
  if (bytes.length < 14 || !["GIF87a", "GIF89a"].includes(bytes.toString("ascii", 0, 6))) {
    return null;
  }
  const canvas = dimensions(bytes.readUInt16LE(6), bytes.readUInt16LE(8));
  if (!canvas) return null;
  const packed = bytes[10]!;
  const hasGlobalPalette = (packed & 0x80) !== 0;
  let offset = 13 + (hasGlobalPalette ? 3 * (2 ** ((packed & 0x07) + 1)) : 0);
  if (offset > bytes.length) return null;
  let frames = 0;

  while (offset < bytes.length) {
    const introducer = bytes[offset++]!;
    if (introducer === 0x3b) {
      return frames === 1 && offset === bytes.length ? canvas : null;
    }
    if (introducer === 0x21) {
      const label = bytes[offset++];
      if (label === undefined) return null;
      if (label === 0xf9) {
        if (bytes[offset] !== 4 || offset + 6 > bytes.length || bytes[offset + 5] !== 0) return null;
        offset += 6;
        continue;
      }
      const extension = readGifSubBlocks(bytes, offset, false);
      if (!extension) return null;
      offset = extension.nextOffset;
      continue;
    }
    if (introducer !== 0x2c || frames > 0 || offset + 9 > bytes.length) return null;

    const left = bytes.readUInt16LE(offset);
    const top = bytes.readUInt16LE(offset + 2);
    const width = bytes.readUInt16LE(offset + 4);
    const height = bytes.readUInt16LE(offset + 6);
    const imagePacked = bytes[offset + 8]!;
    offset += 9;
    if (
      !dimensions(width, height) ||
      left + width > canvas.width ||
      top + height > canvas.height
    ) return null;
    const hasLocalPalette = (imagePacked & 0x80) !== 0;
    if (!hasGlobalPalette && !hasLocalPalette) return null;
    if (hasLocalPalette) {
      offset += 3 * (2 ** ((imagePacked & 0x07) + 1));
      if (offset > bytes.length) return null;
    }
    const minimumCodeSize = bytes[offset++];
    if (minimumCodeSize === undefined || minimumCodeSize < 2 || minimumCodeSize > 8) return null;
    const raster = readGifSubBlocks(bytes, offset, true);
    if (!raster || !validateGifLzw(raster.data, minimumCodeSize, width * height)) return null;
    offset = raster.nextOffset;
    frames += 1;
  }
  return null;
}

function readGifSubBlocks(
  bytes: Buffer,
  initialOffset: number,
  collect: boolean,
): { nextOffset: number; data: Buffer } | null {
  let offset = initialOffset;
  const blocks: Buffer[] = [];
  while (offset < bytes.length) {
    const length = bytes[offset++]!;
    if (length === 0) {
      return {
        nextOffset: offset,
        data: collect ? Buffer.concat(blocks) : Buffer.alloc(0),
      };
    }
    if (offset + length > bytes.length) return null;
    if (collect) blocks.push(bytes.subarray(offset, offset + length));
    offset += length;
  }
  return null;
}

function validateGifLzw(data: Buffer, minimumCodeSize: number, expectedPixels: number): boolean {
  const clearCode = 1 << minimumCodeSize;
  const endCode = clearCode + 1;
  const lengths = new Uint16Array(4096);
  let bitOffset = 0;
  let codeSize = minimumCodeSize + 1;
  let nextCode = endCode + 1;
  let previousCode: number | null = null;
  let pixels = 0;

  const reset = () => {
    lengths.fill(0);
    for (let code = 0; code < clearCode; code += 1) lengths[code] = 1;
    codeSize = minimumCodeSize + 1;
    nextCode = endCode + 1;
    previousCode = null;
  };
  const readCode = (): number | null => {
    if (bitOffset + codeSize > data.length * 8) return null;
    let code = 0;
    for (let bit = 0; bit < codeSize; bit += 1) {
      const absoluteBit = bitOffset + bit;
      code |= ((data[absoluteBit >>> 3]! >>> (absoluteBit & 7)) & 1) << bit;
    }
    bitOffset += codeSize;
    return code;
  };

  reset();
  while (true) {
    const code = readCode();
    if (code === null) return false;
    if (code === clearCode) {
      reset();
      continue;
    }
    if (code === endCode) return pixels === expectedPixels;
    if (code > nextCode || (code === nextCode && previousCode === null)) return false;
    const outputLength = code === nextCode
      ? lengths[previousCode!]! + 1
      : lengths[code]!;
    if (outputLength === 0 || pixels + outputLength > expectedPixels) return false;
    pixels += outputLength;

    if (previousCode !== null && nextCode < 4096) {
      lengths[nextCode] = lengths[previousCode]! + 1;
      nextCode += 1;
      if (nextCode === 1 << codeSize && codeSize < 12) codeSize += 1;
    }
    previousCode = code;
  }
}

function validateWebp(bytes: Buffer): { width: number; height: number } | null {
  if (
    bytes.length < 20 ||
    bytes.toString("ascii", 0, 4) !== "RIFF" ||
    bytes.toString("ascii", 8, 12) !== "WEBP" ||
    bytes.readUInt32LE(4) + 8 !== bytes.length
  ) return null;

  let offset = 12;
  let chunkIndex = 0;
  let canvas: { width: number; height: number } | null = null;
  let frame: { width: number; height: number } | null = null;
  while (offset + 8 <= bytes.length) {
    const chunk = bytes.toString("ascii", offset, offset + 4);
    const chunkLength = bytes.readUInt32LE(offset + 4);
    const dataStart = offset + 8;
    const dataEnd = dataStart + chunkLength;
    const nextOffset = dataEnd + (chunkLength & 1);
    if (dataEnd < dataStart || nextOffset > bytes.length) return null;
    const payload = bytes.subarray(dataStart, dataEnd);

    if (chunk === "VP8X") {
      if (chunkIndex !== 0 || canvas || chunkLength !== 10 || (payload[0]! & 0xc3) !== 0) return null;
      if ((payload[0]! & 0x02) !== 0) return null;
      canvas = dimensions(1 + readUInt24LE(payload, 4), 1 + readUInt24LE(payload, 7));
      if (!canvas) return null;
    } else if (chunk === "ANIM" || chunk === "ANMF") {
      return null;
    } else if (chunk === "VP8 ") {
      if (frame || payload.length < 10) return null;
      const frameTag = readUInt24LE(payload, 0);
      const firstPartitionLength = frameTag >>> 5;
      if (
        (frameTag & 1) !== 0 ||
        payload[3] !== 0x9d || payload[4] !== 0x01 || payload[5] !== 0x2a ||
        firstPartitionLength === 0 ||
        10 + firstPartitionLength >= payload.length
      ) return null;
      frame = dimensions(payload.readUInt16LE(6) & 0x3fff, payload.readUInt16LE(8) & 0x3fff);
      if (!frame) return null;
    } else if (chunk === "VP8L") {
      if (frame || payload.length <= 5 || payload[0] !== 0x2f) return null;
      const bits = payload.readUInt32LE(1);
      if ((bits >>> 29) !== 0) return null;
      frame = dimensions((bits & 0x3fff) + 1, ((bits >>> 14) & 0x3fff) + 1);
    }
    offset = nextOffset;
    chunkIndex += 1;
  }

  if (offset !== bytes.length || !frame) return null;
  if (canvas && (canvas.width !== frame.width || canvas.height !== frame.height)) return null;
  return canvas ?? frame;
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
