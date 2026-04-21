/**
 * UTF-8 byte-offset helpers for planner/pipeline boundaries.
 *
 * DetectorPlugin ranges use tree-sitter-compatible byte offsets. JavaScript
 * strings are indexed by UTF-16 code units, so every final string slice must
 * pass through this conversion when source text may contain non-ASCII bytes.
 */

export function byteOffsetToStringIndex(bytes: Buffer, byteOffset: number): number {
  const clamped = Math.max(0, Math.min(byteOffset, bytes.length));
  return bytes.subarray(0, clamped).toString("utf-8").length;
}

export function sliceByByteRange(bytes: Buffer, startByte: number, endByte: number): string {
  const text = bytes.toString("utf-8");
  const startIndex = byteOffsetToStringIndex(bytes, startByte);
  const endIndex = byteOffsetToStringIndex(bytes, endByte);
  return text.slice(startIndex, endIndex);
}
