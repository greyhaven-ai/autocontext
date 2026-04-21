/**
 * A2-I Layer 6 — dry-run mode (spec §7.3).
 *
 * Writes the session directory layout (spec §9.1):
 *   .autocontext/instrument-patches/<sessionUlid>/
 *     session.json
 *     detections.jsonl
 *     plan.json
 *     patches/
 *       <NNNN>.<flattened-path>.patch
 *     pr-body.md
 *
 * No working-tree mutations. `plan.json` is passed in pre-serialized (caller
 * computed canonical JSON + sha256 already) so determinism is end-to-end and
 * the test can assert byte-identical output across runs.
 *
 * Import discipline: this file imports only from `node:fs`/`node:path` and the
 * contract layer for types. It never reaches scanner/safety/planner — the
 * orchestrator prepared the payload already.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { canonicalJsonStringify } from "../../../contract/canonical-json.js";
import type { InstrumentPlan, InstrumentSession } from "../../contract/plugin-interface.js";

export interface DryRunModeInputs {
  readonly sessionDir: string;
  readonly session: InstrumentSession;
  readonly plan: InstrumentPlan;
  /** Pre-serialized canonical JSON of `plan` — caller pre-hashes this for plan-hash. */
  readonly planJson: string;
  readonly detections: readonly DetectionLine[];
  readonly patches: readonly { readonly filePath: string; readonly patch: string }[];
  readonly prBody: string;
}

export interface DetectionLine {
  readonly pluginId: string;
  readonly filePath: string;
  readonly matchRange: { readonly startByte: number; readonly endByte: number };
  readonly editsProduced: number;
}

export function runDryRunMode(inputs: DryRunModeInputs): void {
  mkdirSync(inputs.sessionDir, { recursive: true });
  mkdirSync(join(inputs.sessionDir, "patches"), { recursive: true });

  // session.json — NOT byte-deterministic across invocations (contains
  // timestamps + ULID) but IS byte-deterministic given the same injected
  // nowIso + sessionUlid. We still canonical-stringify for key-order stability.
  writeFileSync(
    join(inputs.sessionDir, "session.json"),
    canonicalJsonStringify(inputs.session as unknown) + "\n",
    "utf-8",
  );

  // detections.jsonl — one line per plugin.produce() call.
  const detectLines = inputs.detections.map((d) => canonicalJsonStringify(d as unknown)).join("\n");
  writeFileSync(
    join(inputs.sessionDir, "detections.jsonl"),
    detectLines + (detectLines.length > 0 ? "\n" : ""),
    "utf-8",
  );

  // plan.json — byte-deterministic given the same inputs.
  writeFileSync(join(inputs.sessionDir, "plan.json"), inputs.planJson + "\n", "utf-8");

  // patches/<NNNN>.<flattened-path>.patch — write one patch file per affected file.
  for (let i = 0; i < inputs.patches.length; i += 1) {
    const p = inputs.patches[i]!;
    const seq = String(i + 1).padStart(4, "0");
    const flat = flattenPath(p.filePath);
    writeFileSync(
      join(inputs.sessionDir, "patches", `${seq}.${flat}.patch`),
      p.patch,
      "utf-8",
    );
  }

  // pr-body.md — rendered narrative.
  writeFileSync(join(inputs.sessionDir, "pr-body.md"), inputs.prBody, "utf-8");
}

function flattenPath(p: string): string {
  // Preserve characters that are safe in filenames. `/` → `.` flattens so the
  // pr-body table maps cleanly to patch-file names.
  return p.replace(/^\.+/, "").replace(/\//g, ".");
}
