import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, sep } from "node:path";
import { computeTreeHash, type TreeFile } from "../contract/invariants.js";
import type { ContentHash } from "../contract/branded-ids.js";

/**
 * Compute the content-addressable hash of a directory by reading every file
 * recursively and delegating to `computeTreeHash`. Paths are normalized to
 * POSIX form (forward slashes) so the hash is stable across platforms.
 *
 * Symlinks are not followed; only regular files are included.
 */
export function hashDirectory(dir: string): ContentHash {
  const files: TreeFile[] = [];
  walk(dir, "", files);
  return computeTreeHash(files);
}

function walk(absRoot: string, relPrefix: string, out: TreeFile[]): void {
  let entries: string[];
  try {
    entries = readdirSync(join(absRoot, relPrefix));
  } catch {
    return;
  }
  for (const entry of entries) {
    const relPath = relPrefix === "" ? entry : `${relPrefix}/${entry}`;
    const absPath = join(absRoot, relPath.split("/").join(sep));
    const st = statSync(absPath);
    if (st.isDirectory()) {
      walk(absRoot, relPath, out);
    } else if (st.isFile()) {
      out.push({ path: relPath, content: readFileSync(absPath) });
    }
  }
}
