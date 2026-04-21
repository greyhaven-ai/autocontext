/**
 * A2-I Layer 6 — apply mode (spec §7.4).
 *
 * Writes each patch's `afterContent` to the corresponding working-tree path
 * (authoritative final bytes from the composer — the unified-diff surface is
 * for PR rendering, never for re-application). Clean-tree preflight is
 * enforced BEFORE this function is called — orchestrator short-circuits on
 * dirty tree + no --force.
 *
 * Line-ending / encoding discipline (spec §13 risk 1):
 *   The composer renders UTF-8 text; customer files with CRLF or BOM are
 *   handled by the upstream `emitUnifiedDiff` which preserves the byte
 *   sequence passed in. If a customer's file has a BOM, the composer will
 *   have stripped it during `sourceFile.bytes.toString("utf-8")`; that's a
 *   known limitation documented in the Layer 8 concerns report. Apply mode
 *   therefore does not attempt to re-insert BOMs or CRLFs.
 *
 * Writes `apply-log.json` into the session dir per spec §7.4.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { canonicalJsonStringify } from "../../../contract/canonical-json.js";

export interface ApplyModeInputs {
  readonly cwd: string;
  readonly sessionDir: string;
  readonly patches: readonly { readonly filePath: string; readonly afterContent: string }[];
  readonly sessionUlid: string;
  readonly nowIso: string;
}

export interface ApplyModeResult {
  readonly filesWritten: readonly string[];
}

export function runApplyMode(inputs: ApplyModeInputs): ApplyModeResult {
  const written: string[] = [];
  for (const p of inputs.patches) {
    const abs = join(inputs.cwd, p.filePath);
    mkdirSync(dirname(abs), { recursive: true });
    writeFileSync(abs, p.afterContent, "utf-8");
    written.push(p.filePath);
  }

  writeApplyLog({
    sessionDir: inputs.sessionDir,
    sessionUlid: inputs.sessionUlid,
    nowIso: inputs.nowIso,
    filesWritten: written,
    mode: "apply",
  });

  return { filesWritten: written };
}

/** Shared apply-log writer — also used by apply-branch mode. */
export function writeApplyLog(args: {
  readonly sessionDir: string;
  readonly sessionUlid: string;
  readonly nowIso: string;
  readonly filesWritten: readonly string[];
  readonly mode: "apply" | "apply-branch";
  readonly branchName?: string;
  readonly commitSha?: string;
}): void {
  const log = {
    sessionUlid: args.sessionUlid,
    completedAt: args.nowIso,
    mode: args.mode,
    filesWritten: [...args.filesWritten].sort(),
    ...(args.branchName !== undefined ? { branchName: args.branchName } : {}),
    ...(args.commitSha !== undefined ? { commitSha: args.commitSha } : {}),
  };
  writeFileSync(
    join(args.sessionDir, "apply-log.json"),
    canonicalJsonStringify(log as unknown) + "\n",
    "utf-8",
  );
}
