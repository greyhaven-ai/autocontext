import {
  mkdirSync,
  readFileSync,
  writeFileSync,
  readdirSync,
  statSync,
  existsSync,
  cpSync,
  renameSync,
} from "node:fs";
import { join, sep } from "node:path";
import type { ArtifactId } from "../contract/branded-ids.js";
import type { Artifact } from "../contract/types.js";
import { validateArtifact } from "../contract/validators.js";
import { canonicalJsonStringify } from "../contract/canonical-json.js";
import { hashDirectory } from "./content-address.js";

const ROOT = ".autocontext";
const CANDIDATES = "candidates";

function candidateDir(registryRoot: string, id: ArtifactId): string {
  return join(registryRoot, ROOT, CANDIDATES, id);
}

/**
 * Persist an Artifact aggregate to disk:
 *   <registryRoot>/.autocontext/candidates/<id>/
 *     metadata.json   — canonical JSON of the Artifact
 *     payload/        — copy of the source payload directory
 *     payload.sha256  — sidecar containing the canonical "sha256:..." hash
 *
 * Refuses to save if the artifact fails schema validation. Refuses if the
 * directory already exists (artifacts are immutable; use a new id instead).
 */
export function saveArtifact(
  registryRoot: string,
  artifact: Artifact,
  payloadDir: string,
): void {
  const v = validateArtifact(artifact);
  if (!v.valid) {
    throw new Error(`saveArtifact: invalid Artifact: ${v.errors.join("; ")}`);
  }
  const dir = candidateDir(registryRoot, artifact.id);
  if (existsSync(dir)) {
    throw new Error(`saveArtifact: artifact directory already exists at ${dir}`);
  }
  mkdirSync(dir, { recursive: true });

  // Copy the payload directory into <dir>/payload via fs.cpSync.
  const dstPayload = join(dir, "payload");
  cpSync(payloadDir, dstPayload, { recursive: true });

  // Sidecar with the hash for fast read-time check.
  writeFileSync(join(dir, "payload.sha256"), artifact.payloadHash + "\n", "utf-8");

  // Metadata in canonical form for stable bytes (and future signing).
  writeFileSync(join(dir, "metadata.json"), canonicalJsonStringify(artifact), "utf-8");
}

/**
 * Read an Artifact aggregate from disk. Recomputes the payload's tree hash
 * and refuses to return the artifact if it does not match `artifact.payloadHash`
 * (I2 — content addressing).
 */
export function loadArtifact(registryRoot: string, id: ArtifactId): Artifact {
  const dir = candidateDir(registryRoot, id);
  if (!existsSync(dir)) {
    throw new Error(`loadArtifact: artifact ${id} not found at ${dir}`);
  }
  const metaRaw = readFileSync(join(dir, "metadata.json"), "utf-8");
  const artifact = JSON.parse(metaRaw) as Artifact;
  const v = validateArtifact(artifact);
  if (!v.valid) {
    throw new Error(`loadArtifact: stored Artifact failed validation: ${v.errors.join("; ")}`);
  }

  const payloadDir = join(dir, "payload");
  if (existsSync(payloadDir)) {
    const recomputed = hashDirectory(payloadDir);
    if (recomputed !== artifact.payloadHash) {
      throw new Error(
        `loadArtifact: payload hash mismatch for ${id} — expected ${artifact.payloadHash}, got ${recomputed}`,
      );
    }
  }
  return artifact;
}

/**
 * Rewrite the metadata.json for an existing artifact.
 *   - The payload directory is NOT touched.
 *   - The new metadata's payloadHash MUST still match the on-disk payload.
 *     This enforces I2 (content addressing) across mutations to mutable
 *     fields like activationState and promotionHistory.
 *   - Uses tmp-file + rename for atomic replacement.
 *
 * Refuses if the artifact dir doesn't exist or the artifact fails validation.
 */
export function updateArtifactMetadata(registryRoot: string, artifact: Artifact): void {
  const v = validateArtifact(artifact);
  if (!v.valid) {
    throw new Error(`updateArtifactMetadata: invalid Artifact: ${v.errors.join("; ")}`);
  }
  const dir = candidateDir(registryRoot, artifact.id);
  if (!existsSync(dir)) {
    throw new Error(`updateArtifactMetadata: artifact ${artifact.id} not found at ${dir}`);
  }
  const payloadDir = join(dir, "payload");
  if (existsSync(payloadDir)) {
    const recomputed = hashDirectory(payloadDir);
    if (recomputed !== artifact.payloadHash) {
      throw new Error(
        `updateArtifactMetadata: payload hash mismatch — metadata says ${artifact.payloadHash}, on-disk payload hashes to ${recomputed}`,
      );
    }
  }
  const tmp = join(dir, "metadata.json.tmp");
  writeFileSync(tmp, canonicalJsonStringify(artifact), "utf-8");
  renameSync(tmp, join(dir, "metadata.json"));
}

/**
 * List every artifact id present under `<registryRoot>/.autocontext/candidates/`.
 * Returns an empty list if the directory does not exist.
 */
export function listArtifactIds(registryRoot: string): ArtifactId[] {
  const dir = join(registryRoot, ROOT, CANDIDATES);
  if (!existsSync(dir)) return [];
  const out: ArtifactId[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    try {
      if (statSync(full).isDirectory()) {
        out.push(entry as ArtifactId);
      }
    } catch {
      // ignore unreadable entries
    }
  }
  return out;
}

/**
 * Resolve the on-disk directory that holds the artifact's payload + metadata.
 * Useful for store coordinators that need to write per-artifact sub-files
 * (e.g. promotion-history.jsonl, eval-runs/).
 */
export function artifactDirectory(registryRoot: string, id: ArtifactId): string {
  return candidateDir(registryRoot, id);
}

// Re-export for tests / external callers that need to traverse payload.
export { sep as PATH_SEP };
