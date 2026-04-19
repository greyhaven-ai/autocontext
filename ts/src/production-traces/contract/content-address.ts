import { createHash } from "node:crypto";
import type { ContentHash } from "./branded-ids.js";

/**
 * Crockford base32 alphabet: 0-9 A-H J K M N P-T V-Z (excludes I L O U).
 * Same set as ULID's character encoding — see Foundation B branded-ids.
 */
const CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

/**
 * Derive a content-addressed dataset ID per spec §8.5.
 *
 *   datasetId = "ds_" + first 26 chars of sha256(configHash + inputTracesHash)
 *               encoded in Crockford base32
 *
 * Same inputs → byte-identical output (property-tested as P1 foundation).
 * The `ds_` prefix distinguishes content-derived dataset IDs from time-ordered
 * ULIDs used elsewhere (ArtifactId, ProductionTraceId).
 */
export function deriveDatasetId(
  configHash: ContentHash,
  inputTracesHash: ContentHash,
): string {
  const digest = createHash("sha256")
    .update(configHash)
    .update(inputTracesHash)
    .digest();
  const encoded = crockfordBase32Encode(digest);
  return "ds_" + encoded.slice(0, 26);
}

/**
 * Crockford base32 encode a byte buffer. Groups 5 bits at a time from the MSB
 * of the concatenated bitstream. Output length is ceil(8 * n / 5).
 *
 * 32 bytes of SHA-256 → 256 bits → ceil(256/5) = 52 Crockford chars. The caller
 * takes the first 26 of those 52.
 */
function crockfordBase32Encode(buf: Buffer): string {
  let bits = 0;
  let value = 0;
  let out = "";
  for (const b of buf) {
    value = (value << 8) | b;
    bits += 8;
    while (bits >= 5) {
      bits -= 5;
      const idx = (value >>> bits) & 0x1f;
      out += CROCKFORD_ALPHABET[idx];
    }
  }
  if (bits > 0) {
    const idx = (value << (5 - bits)) & 0x1f;
    out += CROCKFORD_ALPHABET[idx];
  }
  return out;
}
