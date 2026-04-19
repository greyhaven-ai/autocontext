// Public surface for the production-traces ingest sub-context.

export { acquireLock } from "./lock.js";
export type { LockHandle } from "./lock.js";

export {
  productionTracesRoot,
  incomingDir,
  ingestedDir,
  failedDir,
  seenIdsPath,
  gcLogPath,
  dateOf,
} from "./paths.js";

export {
  loadSeenIds,
  appendSeenId,
  rebuildSeenIdsFromIngested,
} from "./dedupe.js";

export { validateIngestedLine } from "./validator.js";
export type { IngestLineResult } from "./validator.js";

export {
  markRedactions,
  applyRedactions,
  loadRedactionPolicy,
  loadInstallSalt,
} from "./redaction-phase.js";
export type { LoadedRedactionPolicy } from "./redaction-phase.js";

export { writeReceipt, writeErrorFile } from "./receipt.js";
export type { ReceiptFields, ErrorFileFields, PerLineError } from "./receipt.js";

export { ingestBatches } from "./scan-workflow.js";
export type { IngestOpts, IngestReport } from "./scan-workflow.js";
