// Shared helper: "write one file from <payloadDir>/<payloadFileName> to
// <resolvedTargetPath> in the working tree", first verifying the on-disk payload
// tree hash matches `artifact.payloadHash`. Used by all four concrete actuators.
//
// Import discipline (§3.2): this module is in actuators/ and so imports ONLY from
// contract/ (and Node core). It re-implements the `hashDirectory` walk here
// rather than pulling it from registry/.

import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  statSync,
} from "node:fs";
import { dirname, join, sep } from "node:path";
import type { Artifact } from "../../contract/types.js";
import { computeTreeHash, type TreeFile } from "../../contract/invariants.js";

export interface ApplySingleFileInputs {
  readonly artifact: Artifact;
  /** Absolute path to the on-disk payload directory (e.g. `<candidatesDir>/payload`). */
  readonly payloadDir: string;
  /** Name of the single file within the payload tree to copy (e.g. "prompt.txt"). */
  readonly payloadFileName: string;
  /** Absolute path in the working tree to write the file to. */
  readonly resolvedTargetPath: string;
}

/** Walk `dir` and collect every regular file as a TreeFile (POSIX relative paths). */
function walk(dir: string): TreeFile[] {
  const out: TreeFile[] = [];
  function recurse(absPrefix: string, relPrefix: string): void {
    let entries: string[];
    try {
      entries = readdirSync(absPrefix);
    } catch {
      return;
    }
    for (const entry of entries) {
      const relPath = relPrefix === "" ? entry : `${relPrefix}/${entry}`;
      const absPath = join(absPrefix, entry);
      const st = statSync(absPath);
      if (st.isDirectory()) {
        recurse(absPath, relPath);
      } else if (st.isFile()) {
        out.push({ path: relPath.split(sep).join("/"), content: readFileSync(absPath) });
      }
    }
  }
  recurse(dir, "");
  return out;
}

/**
 * Verify `payloadDir` hashes to `artifact.payloadHash` and then copy
 * `<payloadDir>/<payloadFileName>` to `resolvedTargetPath`. Intermediate
 * directories are created as needed. Throws a descriptive error if:
 *   - the payload tree hash does not match (I2 — content addressing)
 *   - the named payload file is missing from the payload tree
 */
export function applySingleFile(inputs: ApplySingleFileInputs): void {
  const { artifact, payloadDir, payloadFileName, resolvedTargetPath } = inputs;

  // I2 — content addressing — verify before writing anything.
  const files = walk(payloadDir);
  const recomputed = computeTreeHash(files);
  if (recomputed !== artifact.payloadHash) {
    throw new Error(
      `applySingleFile(${artifact.id}): payload hash mismatch — `
      + `expected ${artifact.payloadHash}, on-disk payload hashes to ${recomputed}`,
    );
  }

  const src = join(payloadDir, payloadFileName);
  if (!existsSync(src)) {
    throw new Error(
      `applySingleFile(${artifact.id}): payload file '${payloadFileName}' missing from ${payloadDir}`,
    );
  }

  mkdirSync(dirname(resolvedTargetPath), { recursive: true });
  copyFileSync(src, resolvedTargetPath);
}
