// Public surface of the retention sub-module (spec §6.6).
//
// Owns retention policy I/O, the "enforce retention" operation (the core
// domain verb), and the append-only gc-log audit. Ingest (`ingest/scan-
// workflow.ts`) wires `enforceRetention` as a phase-2 step inside the same
// lock scope; `cli/prune.ts` is a thin CLI wrapper over this module.
//
// Vocabulary is taken verbatim from spec §6.6 — retentionDays, preserveAll,
// preserveCategories, gcBatchSize, gc-log.jsonl.

export type {
  RetentionPolicy,
  LoadedRetentionPolicy,
} from "./policy.js";
export {
  loadRetentionPolicy,
  saveRetentionPolicy,
  defaultRetentionPolicy,
  retentionPolicyPath,
} from "./policy.js";

export type { RetentionInputs, RetentionReport, GcLogEntry } from "./enforce.js";
export { enforceRetention } from "./enforce.js";

export { appendGcLogEntry, readGcLog } from "./gc-log.js";
