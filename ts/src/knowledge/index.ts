export { SkillPackage, exportAgentTaskSkill, cleanLessons } from "./skill-package.js";
export type { SkillPackageData } from "./skill-package.js";
export { HarnessStore } from "./harness-store.js";
export type { HarnessVersionEntry, HarnessVersionMap } from "./harness-store.js";

// AC-344: Knowledge system
export { VersionedFileStore } from "./versioned-store.js";
export type { VersionedFileStoreOpts } from "./versioned-store.js";
export {
  PlaybookManager,
  PlaybookGuard,
  EMPTY_PLAYBOOK_SENTINEL,
  PLAYBOOK_MARKERS,
} from "./playbook.js";
export type { GuardResult } from "./playbook.js";
export { ArtifactStore } from "./artifact-store.js";
export type { AppendedCompactionEntries, ArtifactStoreOpts } from "./artifact-store.js";
export {
  approvePendingPlaybook,
  readPendingPlaybook,
  rejectPendingPlaybook,
  stagePendingPlaybook,
} from "./playbook-approval.js";
export type { PendingPlaybookProvenance, PendingPlaybookView } from "./playbook-approval.js";
export { CompactionLedgerStore } from "./compaction-ledger.js";
export type { CompactionEntry } from "./compaction-ledger.js";
export {
  compactPromptComponent,
  compactPromptComponents,
  compactPromptComponentsWithEntries,
  compactionEntriesForComponents,
  clearPromptCompactionCache,
  extractPromotableLines,
  promptCompactionCacheStats,
} from "./semantic-compaction.js";
export type { PromptCompactionOptions, PromptCompactionResult } from "./semantic-compaction.js";
export { buildContextSelectionReport, ContextSelectionReport } from "./context-selection-report.js";
export type {
  ContextSelectionCandidateInput,
  ContextSelectionDecisionInput,
  ContextSelectionDiagnostic,
  ContextSelectionDiagnosticPolicy,
  ContextSelectionReportPayload,
  ContextSelectionReportSummary,
  ContextSelectionStageSummary,
  ContextSelectionTelemetryCard,
} from "./context-selection-report.js";
export {
  STRUCTURAL_HINT_POLICY,
  buildHintAbReport,
  buildHintMetadata,
  effectiveHintStyle,
  structuralHintPrompt,
} from "./soft-hints.js";
export type { HintAbReportRow, HintAbStyleSummary, HintStyle } from "./soft-hints.js";
export { ScoreTrajectoryBuilder } from "./trajectory.js";
export type { TrajectoryRow } from "./trajectory.js";
export { exportStrategyPackage, importStrategyPackage } from "./package.js";
export type {
  StrategyPackageData,
  ImportStrategyPackageResult,
  ConflictPolicy,
} from "./package.js";
