import { createHash } from "node:crypto";
import type { ArtifactId, ContentHash } from "./branded-ids.js";
import type { PromotionEvent, ValidationResult } from "./types.js";

/**
 * I4 — Lineage DAG: a new artifact's parents plus its own id must not form a cycle
 * under the current parent lookup. `lookup(parentId)` returns that artifact's parents,
 * or null if the id is unknown (treated as a leaf).
 */
export function validateLineageNoCycles(
  selfId: ArtifactId,
  parents: readonly ArtifactId[],
  lookup: (id: ArtifactId) => readonly ArtifactId[] | null,
): ValidationResult {
  // BFS/DFS upward through ancestors; if we ever reach selfId, it's a cycle.
  const visited = new Set<ArtifactId>();
  const stack: ArtifactId[] = [...parents];
  while (stack.length > 0) {
    const current = stack.pop()!;
    if (current === selfId) {
      return { valid: false, errors: [`lineage cycle: ${selfId} is its own ancestor via ${current}`] };
    }
    if (visited.has(current)) continue;
    visited.add(current);
    const ancestors = lookup(current);
    if (ancestors) stack.push(...ancestors);
  }
  return { valid: true };
}

/**
 * I3 — Append-only history: `next` must be `prev` plus zero or more additional events.
 * Existing events cannot be mutated, removed, or reordered.
 */
export function validateAppendOnly(
  prev: readonly PromotionEvent[],
  next: readonly PromotionEvent[],
): ValidationResult {
  if (next.length < prev.length) {
    return { valid: false, errors: [`next history (${next.length}) is shorter than prev (${prev.length})`] };
  }
  for (let i = 0; i < prev.length; i++) {
    if (!eventsEqual(prev[i], next[i])) {
      return { valid: false, errors: [`event at index ${i} has been mutated or reordered`] };
    }
  }
  return { valid: true };
}

function eventsEqual(a: PromotionEvent, b: PromotionEvent): boolean {
  // Deep-equal via JSON — PromotionEvents are plain JSON-serializable values,
  // so key order differences would only matter if a caller constructed them that way.
  // Use canonical comparison for safety.
  return (
    a.from === b.from
    && a.to === b.to
    && a.reason === b.reason
    && a.timestamp === b.timestamp
    && a.signature === b.signature
    && JSON.stringify(a.evidence ?? null) === JSON.stringify(b.evidence ?? null)
  );
}

// ---- Content addressing ----

export interface TreeFile {
  readonly path: string;    // repo-relative posix path; forward slashes
  readonly content: Uint8Array;
}

/**
 * Compute the SHA-256 tree hash of a set of files.
 *
 * Algorithm (deterministic, portable, git-compatible in spirit):
 *   tree_hash = sha256( concat over (path asc) of: <path> \0 <file_sha256> \n )
 *
 * Returns "sha256:<64 hex>".
 */
export function computeTreeHash(files: readonly TreeFile[]): ContentHash {
  const seen = new Set<string>();
  for (const f of files) {
    if (seen.has(f.path)) {
      throw new Error(`computeTreeHash: duplicate path '${f.path}' in input`);
    }
    seen.add(f.path);
  }

  const sorted = [...files].sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));

  const tree = createHash("sha256");
  for (const f of sorted) {
    const fileHash = sha256Hex(f.content);
    tree.update(f.path);
    tree.update(Buffer.from([0])); // NUL separator
    tree.update(fileHash);
    tree.update("\n");
  }
  return ("sha256:" + tree.digest("hex")) as ContentHash;
}

function sha256Hex(data: Uint8Array): string {
  return createHash("sha256").update(data).digest("hex");
}
