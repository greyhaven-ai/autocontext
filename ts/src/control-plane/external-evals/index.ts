import type {
  EvalRunIntegrity,
  EvalTrial,
  EvalTrialStatus,
  ValidationResult,
} from "../contract/types.js";
import type {
  OperationalMemoryFinding,
  OperationalMemoryPack,
} from "../memory-packs/index.js";

export type ExternalEvalAdapterLifecycleStatus =
  | "not-started"
  | "running"
  | "completed"
  | "failed"
  | "timed-out"
  | "cancelled";

export type ExternalEvalBoundaryPolicyMode = "report-only" | "discard";
export type ExternalEvalBoundaryAccessKind =
  | "read"
  | "write"
  | "list"
  | "execute"
  | "search"
  | "unknown";
export type ExternalEvalBoundaryObservationSource =
  | "adapter-command"
  | "adapter-log"
  | "tool-call"
  | "trace"
  | "manual";
export type ExternalEvalBoundaryViolationReason =
  | "blocked-path-prefix"
  | "outside-allowed-path-prefix";

export type ExternalEvalDiagnosticCategory =
  | "agent-task-failure"
  | "verifier-contract-mismatch"
  | "setup-environment-failure"
  | "adapter-runtime-failure"
  | "integrity-risk"
  | "unknown";

export type ExternalEvalImprovementSignalKind =
  | "required-artifact-contract"
  | "change-surface-discipline"
  | "domain-correctness-validation"
  | "exact-verifier-command"
  | "consumer-path-parity";

export interface ExternalEvalTokenUsage {
  readonly input: number;
  readonly output: number;
}

export interface ExternalEvalAdapterCommand {
  readonly argv: readonly string[];
  readonly cwd: string;
}

export interface ExternalEvalAdapterArtifacts {
  readonly stdoutPath?: string;
  readonly stderrPath?: string;
  readonly finalMessagePath?: string;
  readonly tokens?: ExternalEvalTokenUsage;
}

export interface ExternalEvalAdapterLifecycle {
  readonly runId: string;
  readonly taskId: string;
  readonly trialId: string;
  readonly adapter: string;
  readonly command: ExternalEvalAdapterCommand;
  readonly status: ExternalEvalAdapterLifecycleStatus;
  readonly pid?: number;
  readonly exitCode?: number;
  readonly signal?: string;
  readonly timeoutSource?: string;
  readonly errorKind?: string;
  readonly startedAt?: string;
  readonly endedAt?: string;
  readonly artifacts: ExternalEvalAdapterArtifacts;
}

export interface ExternalEvalBoundaryPolicy {
  readonly mode: ExternalEvalBoundaryPolicyMode;
  readonly blockedPathPrefixes?: readonly string[];
  readonly allowedPathPrefixes?: readonly string[];
}

export interface ExternalEvalBoundaryObservation {
  readonly trialId: string;
  readonly accessKind: ExternalEvalBoundaryAccessKind;
  readonly path: string;
  readonly source: ExternalEvalBoundaryObservationSource;
  readonly command?: string;
}

export interface ExternalEvalBoundaryViolation extends ExternalEvalBoundaryObservation {
  readonly reason: ExternalEvalBoundaryViolationReason;
}

export interface ExternalEvalBoundaryAssessment {
  readonly status: EvalRunIntegrity["status"];
  readonly mode: ExternalEvalBoundaryPolicyMode;
  readonly violations: readonly ExternalEvalBoundaryViolation[];
  readonly notes: readonly string[];
}

export interface AssessExternalEvalBoundaryPolicyInputs {
  readonly policy: ExternalEvalBoundaryPolicy;
  readonly observations: readonly ExternalEvalBoundaryObservation[];
}

export interface ClassifyExternalEvalTrialInputs {
  readonly taskId: string;
  readonly trialId: string;
  readonly attempt: number;
  readonly isResolved: boolean;
  readonly failureMode?: string;
  readonly reward?: number;
  readonly startedAt?: string;
  readonly completedAt?: string;
  readonly rawResultPath?: string;
  readonly lifecycle?: ExternalEvalAdapterLifecycle;
  readonly boundaryAssessment?: ExternalEvalBoundaryAssessment;
}

export interface ExternalEvalTrialEvidence {
  readonly trialId: string;
  readonly evidenceRefs?: readonly string[];
  readonly verifierOutput?: string;
  readonly adapterLifecycle?: ExternalEvalAdapterLifecycle;
  readonly boundaryAssessment?: ExternalEvalBoundaryAssessment;
}

export interface ExternalEvalTrialDiagnostic {
  readonly id: string;
  readonly runId: string;
  readonly taskId: string;
  readonly trialId: string;
  readonly category: ExternalEvalDiagnosticCategory;
  readonly confidence: number;
  readonly summary: string;
  readonly evidenceRefs: readonly string[];
  readonly failureExcerpts: readonly string[];
  readonly recommendations: readonly string[];
}

export interface ExternalEvalImprovementSignal {
  readonly id: string;
  readonly runId: string;
  readonly kind: ExternalEvalImprovementSignalKind;
  readonly confidence: number;
  readonly summary: string;
  readonly evidenceRefs: readonly string[];
  readonly taskIds: readonly string[];
  readonly trialIds: readonly string[];
  readonly reusableBehavior: string;
  readonly targetFamilies: readonly string[];
  readonly risk: OperationalMemoryFinding["risk"];
}

export interface ExternalEvalDiagnosticReport {
  readonly schemaVersion: "external-eval-diagnostics/v1";
  readonly runId: string;
  readonly createdAt: string;
  readonly diagnostics: readonly ExternalEvalTrialDiagnostic[];
  readonly improvementSignals?: readonly ExternalEvalImprovementSignal[];
  readonly summary: {
    readonly totalTrials: number;
    readonly unresolvedTrials: number;
    readonly runtimeIssueTrials: number;
    readonly countsByCategory: Readonly<Partial<Record<ExternalEvalDiagnosticCategory, number>>>;
  };
}

export interface BuildExternalEvalDiagnosticReportInputs {
  readonly runId: string;
  readonly createdAt: string;
  readonly trials: readonly EvalTrial[];
  readonly evidence?: readonly ExternalEvalTrialEvidence[];
}

export interface BuildOperationalMemoryPackFromDiagnosticsInputs {
  readonly packId: string;
  readonly version: string;
  readonly createdAt: string;
  readonly report: ExternalEvalDiagnosticReport;
}

export function validateExternalEvalAdapterLifecycle(input: unknown): ValidationResult {
  const errors: string[] = [];

  if (!isRecord(input)) {
    return { valid: false, errors: ["adapter lifecycle must be an object"] };
  }

  requireString(input, "runId", errors);
  requireString(input, "taskId", errors);
  requireString(input, "trialId", errors);
  requireString(input, "adapter", errors);
  requireEnum(
    input,
    "status",
    ["not-started", "running", "completed", "failed", "timed-out", "cancelled"],
    errors,
  );
  validateCommand(input.command, errors);
  validateArtifacts(input.artifacts, errors);

  if (input.status === "timed-out") {
    requireString(input, "timeoutSource", errors);
  }
  requireOptionalNumber(input, "pid", errors);
  requireOptionalNumber(input, "exitCode", errors);
  requireOptionalString(input, "signal", errors);
  requireOptionalString(input, "errorKind", errors);
  requireOptionalString(input, "startedAt", errors);
  requireOptionalString(input, "endedAt", errors);

  return errors.length === 0 ? { valid: true } : { valid: false, errors };
}

export function validateExternalEvalBoundaryPolicy(input: unknown): ValidationResult {
  const errors: string[] = [];

  if (!isRecord(input)) {
    return { valid: false, errors: ["boundary policy must be an object"] };
  }

  requireEnum(input, "mode", ["report-only", "discard"], errors);
  validateOptionalStringArray(input, "blockedPathPrefixes", errors);
  validateOptionalStringArray(input, "allowedPathPrefixes", errors);

  const blockedCount = Array.isArray(input.blockedPathPrefixes) ? input.blockedPathPrefixes.length : 0;
  const allowedCount = Array.isArray(input.allowedPathPrefixes) ? input.allowedPathPrefixes.length : 0;
  if (blockedCount + allowedCount === 0) {
    errors.push("boundary policy must declare at least one blocked or allowed path prefix");
  }

  return errors.length === 0 ? { valid: true } : { valid: false, errors };
}

export function assessExternalEvalBoundaryPolicy(
  inputs: AssessExternalEvalBoundaryPolicyInputs,
): ExternalEvalBoundaryAssessment {
  const blockedPathPrefixes = normalizeBoundaryPrefixes(inputs.policy.blockedPathPrefixes ?? []);
  const allowedPathPrefixes = normalizeBoundaryPrefixes(inputs.policy.allowedPathPrefixes ?? []);
  const violations = inputs.observations.flatMap((observation) =>
    assessBoundaryObservation(observation, blockedPathPrefixes, allowedPathPrefixes),
  );
  const status =
    violations.length === 0 ? "clean" : inputs.policy.mode === "discard" ? "discarded" : "contaminated";

  return {
    status,
    mode: inputs.policy.mode,
    violations,
    notes: boundaryViolationNotes(violations),
  };
}

export function classifyExternalEvalTrial(inputs: ClassifyExternalEvalTrialInputs): EvalTrial {
  const scopedInputs = scopedClassifyInputs(inputs);
  const status = shouldDiscardBoundaryAssessment(scopedInputs.boundaryAssessment)
    ? "discarded"
    : scopedInputs.isResolved
      ? "passed"
      : classifyUnresolvedStatus(scopedInputs);
  const errorKind = classifyErrorKind(scopedInputs, status);
  const reward = isScoreableTrialStatus(status) ? inputs.reward ?? defaultReward(status) : undefined;
  const notes = buildTrialNotes(scopedInputs);

  return {
    taskId: inputs.taskId,
    trialId: inputs.trialId,
    attempt: inputs.attempt,
    status,
    ...(reward !== undefined ? { reward } : {}),
    ...(errorKind !== undefined ? { errorKind } : {}),
    ...(inputs.startedAt !== undefined ? { startedAt: inputs.startedAt } : {}),
    ...(inputs.completedAt !== undefined ? { completedAt: inputs.completedAt } : {}),
    ...(inputs.rawResultPath !== undefined ? { rawResultPath: inputs.rawResultPath } : {}),
    ...(notes.length > 0 ? { notes } : {}),
  };
}

function scopedClassifyInputs(inputs: ClassifyExternalEvalTrialInputs): ClassifyExternalEvalTrialInputs {
  const boundaryAssessment = boundaryAssessmentForTrial(inputs.boundaryAssessment, inputs.trialId);
  return {
    taskId: inputs.taskId,
    trialId: inputs.trialId,
    attempt: inputs.attempt,
    isResolved: inputs.isResolved,
    ...(inputs.failureMode !== undefined ? { failureMode: inputs.failureMode } : {}),
    ...(inputs.reward !== undefined ? { reward: inputs.reward } : {}),
    ...(inputs.startedAt !== undefined ? { startedAt: inputs.startedAt } : {}),
    ...(inputs.completedAt !== undefined ? { completedAt: inputs.completedAt } : {}),
    ...(inputs.rawResultPath !== undefined ? { rawResultPath: inputs.rawResultPath } : {}),
    ...(inputs.lifecycle !== undefined ? { lifecycle: inputs.lifecycle } : {}),
    ...(boundaryAssessment !== undefined ? { boundaryAssessment } : {}),
  };
}

export function buildExternalEvalDiagnosticReport(
  inputs: BuildExternalEvalDiagnosticReportInputs,
): ExternalEvalDiagnosticReport {
  const evidenceByTrialId = new Map<string, ExternalEvalTrialEvidence>();
  for (const evidence of inputs.evidence ?? []) {
    evidenceByTrialId.set(evidence.trialId, evidence);
  }

  const trialsWithEvidence = inputs.trials.map((trial) => ({
    trial,
    evidence: scopedTrialEvidence(evidenceByTrialId.get(trial.trialId), trial.trialId),
  }));
  const diagnostics = trialsWithEvidence
    .filter(
      ({ trial, evidence }) =>
        trial.status !== "passed" ||
        hasAdapterRuntimeIssue(trial, evidence) ||
        hasBoundaryIntegrityRisk(trial, evidence),
    )
    .map(({ trial, evidence }) => buildTrialDiagnostic(inputs.runId, trial, evidence));
  const improvementSignals = buildImprovementSignals(inputs.runId, diagnostics);
  const countsByCategory = countDiagnosticsByCategory(diagnostics);

  return {
    schemaVersion: "external-eval-diagnostics/v1",
    runId: inputs.runId,
    createdAt: inputs.createdAt,
    diagnostics,
    improvementSignals,
    summary: {
      totalTrials: inputs.trials.length,
      unresolvedTrials: inputs.trials.filter((trial) => trial.status !== "passed").length,
      runtimeIssueTrials: trialsWithEvidence.filter(({ trial, evidence }) =>
        hasRuntimeIssueForSummary(trial, evidence),
      ).length,
      countsByCategory,
    },
  };
}

export function buildExternalEvalImprovementSignals(
  report: Pick<ExternalEvalDiagnosticReport, "runId" | "diagnostics">,
): readonly ExternalEvalImprovementSignal[] {
  return buildImprovementSignals(report.runId, report.diagnostics);
}

export function buildOperationalMemoryPackFromDiagnostics(
  inputs: BuildOperationalMemoryPackFromDiagnosticsInputs,
): OperationalMemoryPack {
  const findings = buildOperationalFindings(inputs.report);
  const integrity: EvalRunIntegrity = {
    status: "clean",
    notes: [
      `Derived from external eval diagnostic report ${inputs.report.runId}.`,
      "Findings are category-level operational guidance and exclude adapter-runtime failures.",
    ],
  };

  return {
    packId: inputs.packId,
    version: inputs.version,
    createdAt: inputs.createdAt,
    status: "sanitized",
    integrity,
    findings,
  };
}

function classifyUnresolvedStatus(inputs: ClassifyExternalEvalTrialInputs): EvalTrialStatus {
  const failureMode = normalizedFailureMode(inputs.failureMode);
  const lifecycleStatus = inputs.lifecycle?.status;

  if (lifecycleStatus === "cancelled") return "cancelled";
  if (
    lifecycleStatus === "timed-out" ||
    lifecycleStatus === "failed" ||
    isInfrastructureFailureMode(failureMode) ||
    isInfrastructureFailureMode(inputs.lifecycle?.errorKind)
  ) {
    return "infrastructure-error";
  }
  return "failed";
}

function classifyErrorKind(
  inputs: ClassifyExternalEvalTrialInputs,
  status: EvalTrialStatus,
): string | undefined {
  if (status === "discarded" && shouldDiscardBoundaryAssessment(inputs.boundaryAssessment)) {
    return "external-eval-boundary-violation";
  }
  if (status === "passed") {
    return normalizedFailureMode(inputs.failureMode) || runtimeLifecycleErrorKind(inputs) || undefined;
  }
  if (status === "failed") {
    return normalizedFailureMode(inputs.failureMode) || undefined;
  }
  const failureMode = normalizedFailureMode(inputs.failureMode);
  return failureMode || runtimeLifecycleErrorKind(inputs);
}

function normalizedFailureMode(failureMode: string | undefined): string {
  return failureMode === undefined || failureMode === "unset" ? "" : failureMode;
}

function runtimeLifecycleErrorKind(inputs: ClassifyExternalEvalTrialInputs): string | undefined {
  const lifecycle = inputs.lifecycle;
  if (lifecycle === undefined) return undefined;
  const errorKind = normalizedFailureMode(lifecycle.errorKind);
  if (errorKind.length > 0) return errorKind;
  const timeoutSource = normalizedFailureMode(lifecycle.timeoutSource);
  if (timeoutSource.length > 0) return timeoutSource;
  return isRuntimeIssueLifecycleStatus(lifecycle.status) ? lifecycle.status : undefined;
}

function defaultReward(status: EvalTrialStatus): number | undefined {
  if (status === "passed") return 1;
  if (status === "failed") return 0;
  return undefined;
}

function isScoreableTrialStatus(status: EvalTrialStatus): boolean {
  return status === "passed" || status === "failed";
}

function buildTrialNotes(inputs: ClassifyExternalEvalTrialInputs): readonly string[] {
  const notes: string[] = [];
  const failureMode = normalizedFailureMode(inputs.failureMode);
  if (failureMode.length > 0) {
    notes.push(`failure_mode=${failureMode}`);
  }
  if (inputs.lifecycle !== undefined) {
    notes.push(`adapter_status=${inputs.lifecycle.status}`);
    if (inputs.lifecycle.timeoutSource !== undefined) {
      notes.push(`timeout_source=${inputs.lifecycle.timeoutSource}`);
    }
    if (inputs.lifecycle.errorKind !== undefined) {
      notes.push(`adapter_error_kind=${inputs.lifecycle.errorKind}`);
    }
    if (inputs.lifecycle.artifacts.stdoutPath !== undefined) {
      notes.push(`stdout=${inputs.lifecycle.artifacts.stdoutPath}`);
    }
    if (inputs.lifecycle.artifacts.stderrPath !== undefined) {
      notes.push(`stderr=${inputs.lifecycle.artifacts.stderrPath}`);
    }
  }
  if (hasBoundaryAssessmentIssue(inputs.boundaryAssessment)) {
    notes.push(`integrity_status=${inputs.boundaryAssessment.status}`);
    notes.push(...inputs.boundaryAssessment.notes);
  }
  return notes;
}

function buildTrialDiagnostic(
  runId: string,
  trial: EvalTrial,
  evidence: ExternalEvalTrialEvidence | undefined,
): ExternalEvalTrialDiagnostic {
  const lifecycle = evidence?.adapterLifecycle;
  const category = classifyDiagnosticCategory(trial, evidence);
  const failureExcerpts = buildFailureExcerpts(category, trial, evidence);

  return {
    id: `${runId}:${trial.trialId}`,
    runId,
    taskId: trial.taskId,
    trialId: trial.trialId,
    category,
    confidence: diagnosticConfidence(category, trial, evidence),
    summary: diagnosticSummary(category),
    evidenceRefs: [
      ...(trial.rawResultPath !== undefined ? [trial.rawResultPath] : []),
      ...(evidence?.evidenceRefs ?? []),
      ...(lifecycle?.artifacts.stdoutPath !== undefined ? [lifecycle.artifacts.stdoutPath] : []),
      ...(lifecycle?.artifacts.stderrPath !== undefined ? [lifecycle.artifacts.stderrPath] : []),
    ],
    failureExcerpts,
    recommendations: diagnosticRecommendations(category),
  };
}

function classifyDiagnosticCategory(
  trial: EvalTrial,
  evidence: ExternalEvalTrialEvidence | undefined,
): ExternalEvalDiagnosticCategory {
  if (trial.status === "discarded" || hasBoundaryIntegrityRisk(trial, evidence)) {
    return "integrity-risk";
  }
  if (
    trial.status === "infrastructure-error" ||
    hasAdapterRuntimeIssue(trial, evidence)
  ) {
    return "adapter-runtime-failure";
  }
  if (trial.status === "cancelled") {
    return "adapter-runtime-failure";
  }

  const verifierOutput = stripAnsi(evidence?.verifierOutput ?? "").toLowerCase();
  if (
    verifierOutput.includes("branch yet to be born") ||
    verifierOutput.includes("src refspec") ||
    verifierOutput.includes("failed to push") ||
    verifierOutput.includes("failed to clone") ||
    verifierOutput.includes("connection refused") ||
    verifierOutput.includes("permission denied")
  ) {
    return "setup-environment-failure";
  }
  if (
    verifierOutput.includes("missing required") ||
    verifierOutput.includes("required fields") ||
    verifierOutput.includes("not configured") ||
    verifierOutput.includes("could not find") ||
    verifierOutput.includes("expected") ||
    verifierOutput.includes("assertionerror")
  ) {
    return "verifier-contract-mismatch";
  }
  return trial.status === "failed" ? "agent-task-failure" : "unknown";
}

function diagnosticConfidence(
  category: ExternalEvalDiagnosticCategory,
  trial: EvalTrial,
  evidence: ExternalEvalTrialEvidence | undefined,
): number {
  if (category === "integrity-risk" && hasBoundaryIntegrityRisk(trial, evidence)) return 0.95;
  if (category === "adapter-runtime-failure" && trial.status === "infrastructure-error") return 0.95;
  if (category === "adapter-runtime-failure" && hasAdapterRuntimeIssue(trial, evidence)) return 0.9;
  if (category === "integrity-risk" && trial.status === "discarded") return 0.9;
  if (evidence?.verifierOutput !== undefined && evidence.verifierOutput.length > 0) return 0.85;
  return category === "unknown" ? 0.25 : 0.6;
}

function diagnosticSummary(category: ExternalEvalDiagnosticCategory): string {
  switch (category) {
    case "adapter-runtime-failure":
      return "The trial reported adapter or runtime failure metadata that should be isolated from task-quality scoring.";
    case "setup-environment-failure":
      return "The trial failed while preparing or validating environment state used by the consumer or verifier.";
    case "verifier-contract-mismatch":
      return "The trial output diverged from the verifier-facing contract or checked artifact location.";
    case "agent-task-failure":
      return "The trial reached the verifier and failed without an obvious adapter or setup signature.";
    case "integrity-risk":
      return "The trial has evidence that may affect evaluation integrity.";
    case "unknown":
      return "The trial failed, but available evidence is insufficient to classify it confidently.";
  }
}

function diagnosticRecommendations(category: ExternalEvalDiagnosticCategory): readonly string[] {
  switch (category) {
    case "adapter-runtime-failure":
      return [
        "Preserve adapter lifecycle logs and timeout metadata before scoring the trial.",
        "Treat adapter timeouts separately from normal task failures in score reconciliation.",
      ];
    case "setup-environment-failure":
      return [
        "Validate a fresh consumer path using the same entrypoints, branches, credentials, and paths the verifier will use.",
        "Avoid relying only on manual smoke tests that bypass downstream setup state.",
      ];
    case "verifier-contract-mismatch":
      return [
        "Inspect the verifier-facing artifact and configuration locations before declaring completion.",
        "Mirror the consumer or verifier path rather than checking only runtime behavior.",
      ];
    case "agent-task-failure":
      return [
        "Use the verifier output to identify the smallest missed requirement before retrying.",
      ];
    case "integrity-risk":
      return [
        "Discard or quarantine the trial until integrity status is resolved.",
      ];
    case "unknown":
      return [
        "Capture richer adapter and verifier evidence before converting this failure into persistent guidance.",
      ];
  }
}

function buildFailureExcerpts(
  category: ExternalEvalDiagnosticCategory,
  trial: EvalTrial,
  evidence: ExternalEvalTrialEvidence | undefined,
): readonly string[] {
  if (category === "adapter-runtime-failure") {
    return buildAdapterRuntimeExcerpts(trial, evidence?.adapterLifecycle);
  }
  if (category === "integrity-risk") {
    const boundaryExcerpts = buildBoundaryIntegrityExcerpts(trial, evidence);
    return boundaryExcerpts.length > 0 ? boundaryExcerpts : [
      `trial_status=${trial.status}`,
      ...(trial.errorKind !== undefined ? [`error_kind=${trial.errorKind}`] : []),
      ...(trial.notes ?? []),
    ];
  }

  return sanitizeVerifierOutput(evidence?.verifierOutput ?? "");
}

function buildAdapterRuntimeExcerpts(
  trial: EvalTrial,
  lifecycle: ExternalEvalAdapterLifecycle | undefined,
): readonly string[] {
  const excerpts: string[] = [];
  const add = (excerpt: string | undefined): void => {
    if (excerpt !== undefined && excerpt.length > 0 && !excerpts.includes(excerpt)) {
      excerpts.push(excerpt);
    }
  };

  add(trial.errorKind !== undefined ? `error_kind=${trial.errorKind}` : undefined);
  add(lifecycle !== undefined ? `adapter_status=${lifecycle.status}` : undefined);
  add(lifecycle?.errorKind !== undefined ? `adapter_error_kind=${lifecycle.errorKind}` : undefined);
  add(lifecycle?.timeoutSource !== undefined ? `timeout_source=${lifecycle.timeoutSource}` : undefined);

  for (const note of trial.notes ?? []) {
    if (
      note.startsWith("failure_mode=") ||
      note.startsWith("adapter_status=") ||
      note.startsWith("adapter_error_kind=") ||
      note.startsWith("timeout_source=")
    ) {
      add(note);
    }
  }

  return excerpts;
}

function buildBoundaryIntegrityExcerpts(
  trial: EvalTrial,
  evidence: ExternalEvalTrialEvidence | undefined,
): readonly string[] {
  const assessment = evidence?.boundaryAssessment;
  if (hasBoundaryAssessmentIssue(assessment)) {
    return [`integrity_status=${assessment.status}`, ...assessment.notes];
  }

  const trialNotes = trial.notes ?? [];
  const boundaryNotes = trialNotes.filter(
    (note) => note.startsWith("integrity_status=") || note.startsWith("boundary_violation="),
  );
  return boundaryNotes;
}

function scopedTrialEvidence(
  evidence: ExternalEvalTrialEvidence | undefined,
  trialId: string,
): ExternalEvalTrialEvidence | undefined {
  if (evidence === undefined || evidence.boundaryAssessment === undefined) return evidence;
  const boundaryAssessment = boundaryAssessmentForTrial(evidence.boundaryAssessment, trialId);

  return {
    trialId: evidence.trialId,
    ...(evidence.evidenceRefs !== undefined ? { evidenceRefs: evidence.evidenceRefs } : {}),
    ...(evidence.verifierOutput !== undefined ? { verifierOutput: evidence.verifierOutput } : {}),
    ...(evidence.adapterLifecycle !== undefined ? { adapterLifecycle: evidence.adapterLifecycle } : {}),
    ...(boundaryAssessment !== undefined ? { boundaryAssessment } : {}),
  };
}

function boundaryAssessmentForTrial(
  assessment: ExternalEvalBoundaryAssessment | undefined,
  trialId: string,
): ExternalEvalBoundaryAssessment | undefined {
  if (assessment === undefined) return undefined;
  const violations = assessment.violations.filter((violation) => violation.trialId === trialId);
  if (violations.length === 0) return undefined;

  return {
    status: assessment.mode === "discard" ? "discarded" : "contaminated",
    mode: assessment.mode,
    violations,
    notes: boundaryViolationNotes(violations),
  };
}

function boundaryViolationNotes(
  violations: readonly ExternalEvalBoundaryViolation[],
): readonly string[] {
  return violations.map(
    (violation) => `boundary_violation=${violation.accessKind} ${violation.path} ${violation.reason}`,
  );
}

function hasBoundaryIntegrityRisk(
  trial: EvalTrial,
  evidence: ExternalEvalTrialEvidence | undefined,
): boolean {
  return hasBoundaryAssessmentIssue(evidence?.boundaryAssessment) || hasTrialBoundaryIntegrityNote(trial);
}

function hasBoundaryAssessmentIssue(
  assessment: ExternalEvalBoundaryAssessment | undefined,
): assessment is ExternalEvalBoundaryAssessment {
  return assessment !== undefined && assessment.status !== "clean";
}

function shouldDiscardBoundaryAssessment(
  assessment: ExternalEvalBoundaryAssessment | undefined,
): assessment is ExternalEvalBoundaryAssessment {
  return assessment !== undefined && assessment.status === "discarded";
}

function hasTrialBoundaryIntegrityNote(trial: EvalTrial): boolean {
  return (trial.notes ?? []).some((note) => note.startsWith("boundary_violation="));
}

function assessBoundaryObservation(
  observation: ExternalEvalBoundaryObservation,
  blockedPathPrefixes: readonly string[],
  allowedPathPrefixes: readonly string[],
): readonly ExternalEvalBoundaryViolation[] {
  const path = normalizeBoundaryPath(observation.path);
  if (blockedPathPrefixes.some((prefix) => boundaryPathMatchesPrefix(path, prefix))) {
    return [buildBoundaryViolation(observation, path, "blocked-path-prefix")];
  }
  if (
    allowedPathPrefixes.length > 0 &&
    !allowedPathPrefixes.some((prefix) => boundaryPathMatchesPrefix(path, prefix))
  ) {
    return [buildBoundaryViolation(observation, path, "outside-allowed-path-prefix")];
  }
  return [];
}

function buildBoundaryViolation(
  observation: ExternalEvalBoundaryObservation,
  path: string,
  reason: ExternalEvalBoundaryViolationReason,
): ExternalEvalBoundaryViolation {
  return {
    trialId: observation.trialId,
    accessKind: observation.accessKind,
    path,
    source: observation.source,
    reason,
    ...(observation.command !== undefined ? { command: observation.command } : {}),
  };
}

function normalizeBoundaryPrefixes(prefixes: readonly string[]): readonly string[] {
  return [...new Set(prefixes.map(normalizeBoundaryPath))];
}

function boundaryPathMatchesPrefix(path: string, prefix: string): boolean {
  return prefix === "/" ? path.startsWith("/") : path === prefix || path.startsWith(`${prefix}/`);
}

function normalizeBoundaryPath(input: string): string {
  const raw = input.trim().replace(/\\/g, "/");
  if (raw.length === 0) return ".";

  const absolute = raw.startsWith("/");
  const parts: string[] = [];
  for (const part of raw.split("/")) {
    if (part.length === 0 || part === ".") continue;
    if (part === "..") {
      if (parts.length > 0 && parts[parts.length - 1] !== "..") {
        parts.pop();
      } else if (!absolute) {
        parts.push(part);
      }
      continue;
    }
    parts.push(part);
  }

  const normalized = parts.join("/");
  if (absolute) return normalized.length > 0 ? `/${normalized}` : "/";
  return normalized.length > 0 ? normalized : ".";
}

function isInfrastructureFailureMode(errorKind: string | undefined): boolean {
  const kind = normalizedFailureMode(errorKind).toLowerCase();
  return (
    kind.includes("timeout") ||
    kind.includes("adapter") ||
    kind.includes("runtime") ||
    kind.includes("infrastructure") ||
    kind.includes("subprocess") ||
    kind.includes("process") ||
    kind.includes("container") ||
    kind === "agent_timeout"
  );
}

function hasAdapterRuntimeIssue(
  trial: EvalTrial,
  evidence: ExternalEvalTrialEvidence | undefined,
): boolean {
  const lifecycle = evidence?.adapterLifecycle;
  return (
    isInfrastructureFailureMode(trial.errorKind) ||
    isRuntimeIssueLifecycleStatus(lifecycle?.status) ||
    isInfrastructureFailureMode(lifecycle?.errorKind) ||
    isInfrastructureFailureMode(lifecycle?.timeoutSource)
  );
}

function isRuntimeIssueLifecycleStatus(
  status: ExternalEvalAdapterLifecycleStatus | undefined,
): boolean {
  return status === "failed" || status === "timed-out" || status === "cancelled";
}

function hasRuntimeIssueForSummary(
  trial: EvalTrial,
  evidence: ExternalEvalTrialEvidence | undefined,
): boolean {
  return (
    trial.status === "infrastructure-error" ||
    trial.status === "cancelled" ||
    hasAdapterRuntimeIssue(trial, evidence)
  );
}

function sanitizeVerifierOutput(output: string): readonly string[] {
  const failureExcerpts: string[] = [];
  const fallbackExcerpts: string[] = [];
  const seen = new Set<string>();
  const scanLimit = Math.min(output.length, maxVerifierScanChars);
  let lineStart = 0;
  let scannedLines = 0;

  while (lineStart <= scanLimit && scannedLines < maxVerifierScanLines) {
    const newlineIndex = output.indexOf("\n", lineStart);
    const lineEnd = newlineIndex === -1 ? scanLimit : Math.min(newlineIndex, scanLimit);
    addVerifierExcerpt(output.slice(lineStart, lineEnd), seen, failureExcerpts, fallbackExcerpts);
    scannedLines += 1;

    if (failureExcerpts.length >= maxVerifierExcerpts) break;
    if (newlineIndex === -1 || newlineIndex >= scanLimit) break;
    lineStart = newlineIndex + 1;
  }

  return [...failureExcerpts, ...fallbackExcerpts].slice(0, maxVerifierExcerpts);
}

const maxVerifierExcerpts = 4;
const maxVerifierScanLines = 512;
const maxVerifierScanChars = 256 * 1024;

function addVerifierExcerpt(
  rawLine: string,
  seen: Set<string>,
  failureExcerpts: string[],
  fallbackExcerpts: string[],
): void {
  const line = sanitizeVerifierLine(rawLine);
  if (line.length === 0 || seen.has(line)) return;

  const target = isVerifierFailureLine(line) ? failureExcerpts : fallbackExcerpts;
  if (target.length >= maxVerifierExcerpts) return;

  target.push(line);
  seen.add(line);
}

function isVerifierFailureLine(line: string): boolean {
  return /\b(?:failed|failure|error|fatal|missing|required|assertionerror)\b/i.test(line);
}

function sanitizeVerifierLine(rawLine: string): string {
  const line = stripAnsi(rawLine).replace(/\r$/, "").trim();
  if (line.length === 0) return "";
  if (/expected\b.*\bgot\b/i.test(line)) {
    return "Verifier expected output differed from actual output [values redacted].";
  }
  if (/assert\s+.+==/i.test(line)) {
    return "Verifier assertion failed [expression redacted].";
  }
  return line.length > 280 ? `${line.slice(0, 277)}...` : line;
}

function stripAnsi(input: string): string {
  return input.replace(/\u001b\[[0-9;?]*[ -/]*[@-~]/g, "");
}

function countDiagnosticsByCategory(
  diagnostics: readonly ExternalEvalTrialDiagnostic[],
): Readonly<Partial<Record<ExternalEvalDiagnosticCategory, number>>> {
  const counts = new Map<ExternalEvalDiagnosticCategory, number>();
  for (const diagnostic of diagnostics) {
    counts.set(diagnostic.category, (counts.get(diagnostic.category) ?? 0) + 1);
  }
  return Object.fromEntries(counts.entries()) as Readonly<Partial<Record<ExternalEvalDiagnosticCategory, number>>>;
}

function buildOperationalFindings(
  report: ExternalEvalDiagnosticReport,
): readonly OperationalMemoryFinding[] {
  const findings: OperationalMemoryFinding[] = [];
  const setupDiagnostics = report.diagnostics.filter(
    (diagnostic) => diagnostic.category === "setup-environment-failure",
  );
  const verifierDiagnostics = report.diagnostics.filter(
    (diagnostic) => diagnostic.category === "verifier-contract-mismatch",
  );
  const integrityDiagnostics = report.diagnostics.filter(
    (diagnostic) => diagnostic.category === "integrity-risk",
  );

  if (setupDiagnostics.length > 0) {
    findings.push({
      id: `${report.runId}-setup-environment-failure`,
      summary: "Validate setup through the same consumer path the verifier will use.",
      evidenceRefs: collectEvidenceRefs(setupDiagnostics),
      reusableBehavior:
        "Before declaring setup complete, validate from a fresh consumer or client path with the same branches, entrypoints, credentials, and filesystem paths downstream checks will use.",
      targetFamilies: ["terminal", "service-setup", "stateful-workflow"],
      risk: "medium",
      containsTaskAnswer: false,
      containsSecret: false,
    });
  }
  if (verifierDiagnostics.length > 0) {
    findings.push({
      id: `${report.runId}-verifier-contract-mismatch`,
      summary: "Check artifacts and configuration in verifier-facing locations.",
      evidenceRefs: collectEvidenceRefs(verifierDiagnostics),
      reusableBehavior:
        "Confirm required files, config directives, service routes, and output artifacts in the locations consumed by the checker, not only through manual smoke tests or included fragments.",
      targetFamilies: ["terminal", "service-config", "artifact-contract"],
      risk: "low",
      containsTaskAnswer: false,
      containsSecret: false,
    });
  }
  if (integrityDiagnostics.length > 0) {
    findings.push({
      id: `${report.runId}-benchmark-integrity-boundary`,
      summary: "Keep benchmark verifier-only data outside agent inspection paths.",
      evidenceRefs: collectEvidenceRefs(integrityDiagnostics),
      reusableBehavior:
        "For external benchmark runs, do not list, read, copy, execute, or search verifier-only directories, hidden grader files, benchmark canaries, or solution files; avoid broad filesystem scans unless verifier-only paths are explicitly pruned, and prefer allowlisted task-visible paths.",
      targetFamilies: ["terminal", "external-eval", "benchmark-integrity"],
      risk: "medium",
      containsTaskAnswer: false,
      containsSecret: false,
    });
  }
  for (const signal of buildExternalEvalImprovementSignals(report)) {
    findings.push({
      id: signal.id,
      summary: signal.summary,
      evidenceRefs: signal.evidenceRefs,
      reusableBehavior: signal.reusableBehavior,
      targetFamilies: signal.targetFamilies,
      risk: signal.risk,
      containsTaskAnswer: false,
      containsSecret: false,
    });
  }

  return findings;
}

const improvementSignalKindOrder: readonly ExternalEvalImprovementSignalKind[] = [
  "required-artifact-contract",
  "change-surface-discipline",
  "domain-correctness-validation",
  "exact-verifier-command",
  "consumer-path-parity",
];

function buildImprovementSignals(
  runId: string,
  diagnostics: readonly ExternalEvalTrialDiagnostic[],
): readonly ExternalEvalImprovementSignal[] {
  const diagnosticsByKind = new Map<ExternalEvalImprovementSignalKind, ExternalEvalTrialDiagnostic[]>();

  for (const diagnostic of diagnostics) {
    for (const kind of detectImprovementSignalKinds(diagnostic)) {
      const existing = diagnosticsByKind.get(kind) ?? [];
      diagnosticsByKind.set(kind, [...existing, diagnostic]);
    }
  }

  return improvementSignalKindOrder.flatMap((kind) => {
    const matchingDiagnostics = diagnosticsByKind.get(kind) ?? [];
    if (matchingDiagnostics.length === 0) return [];
    const metadata = improvementSignalMetadata(kind);
    return [
      {
        id: `${runId}-${kind}`,
        runId,
        kind,
        confidence: roundConfidence(
          Math.max(...matchingDiagnostics.map((diagnostic) => diagnostic.confidence), metadata.confidence),
        ),
        summary: metadata.summary,
        evidenceRefs: collectEvidenceRefs(matchingDiagnostics),
        taskIds: [...new Set(matchingDiagnostics.map((diagnostic) => diagnostic.taskId))],
        trialIds: [...new Set(matchingDiagnostics.map((diagnostic) => diagnostic.trialId))],
        reusableBehavior: metadata.reusableBehavior,
        targetFamilies: metadata.targetFamilies,
        risk: metadata.risk,
      },
    ];
  });
}

function detectImprovementSignalKinds(
  diagnostic: ExternalEvalTrialDiagnostic,
): readonly ExternalEvalImprovementSignalKind[] {
  if (
    diagnostic.category === "adapter-runtime-failure" ||
    diagnostic.category === "integrity-risk" ||
    diagnostic.category === "setup-environment-failure" ||
    diagnostic.category === "unknown"
  ) {
    return [];
  }

  const text = normalizeSignalText([
    diagnostic.taskId,
    diagnostic.summary,
    ...diagnostic.failureExcerpts,
  ].join("\n"));
  const kinds = new Set<ExternalEvalImprovementSignalKind>();

  if (
    /(?:test_[a-z0-9_]*(?:file|artifact|output)(?:_[a-z0-9]+)*|(?:file|artifact|output)_exists)["']?\s*[:=]\s*["']?failed/.test(
      text,
    ) ||
    /missing.{0,60}(?:file|artifact|output)/.test(text)
  ) {
    kinds.add("required-artifact-contract");
  }
  if (
    /no_other_files_changed|other files changed|unrelated files|unexpected (?:file|change)|change surface/.test(
      text,
    )
  ) {
    kinds.add("change-surface-discipline");
  }
  if (
    /(?:peak|numeric|tolerance|score|accuracy|prediction)[^,\n}]{0,120}failed/.test(text) ||
    /failed[^,\n}]{0,120}(?:peak|numeric|tolerance|score|accuracy|prediction)/.test(text)
  ) {
    kinds.add("domain-correctness-validation");
  }
  if (
    /(?:compile|compiles|compilation|build|linker|gcc|rustc)[^,\n}]{0,120}failed/.test(text) ||
    /failed[^,\n}]{0,120}(?:compile|compiles|compilation|build|linker|gcc|rustc)/.test(text)
  ) {
    kinds.add("exact-verifier-command");
  }
  if (/(?:branch|deploy|push|clone|https|ssh|consumer path|downstream path)[^,\n}]{0,120}failed/.test(text)) {
    kinds.add("consumer-path-parity");
  }

  return improvementSignalKindOrder.filter((kind) => kinds.has(kind));
}

function improvementSignalMetadata(kind: ExternalEvalImprovementSignalKind): Omit<
  ExternalEvalImprovementSignal,
  "id" | "runId" | "kind" | "confidence" | "evidenceRefs" | "taskIds" | "trialIds"
> & { readonly confidence: number } {
  switch (kind) {
    case "required-artifact-contract":
      return {
        confidence: 0.8,
        summary: "Verify required output artifacts at their checked paths before completion.",
        reusableBehavior:
          "Before finishing, independently confirm every required file or output artifact exists at the exact checked path and that its contents can be read back from that path.",
        targetFamilies: ["terminal", "artifact-contract", "file-output"],
        risk: "low",
      };
    case "change-surface-discipline":
      return {
        confidence: 0.8,
        summary: "Preserve the intended change surface while satisfying the task.",
        reusableBehavior:
          "Snapshot the files that are allowed to change, perform the edit, then compare the final tree against that allowed set before declaring completion.",
        targetFamilies: ["terminal", "repository-maintenance", "safety-cleanup"],
        risk: "medium",
      };
    case "domain-correctness-validation":
      return {
        confidence: 0.75,
        summary: "Validate domain-level correctness, not just output shape.",
        reusableBehavior:
          "When the checker evaluates numeric, scientific, prediction, or scoring quality, add an independent reasonableness check for the measured value instead of stopping at schema or file validation.",
        targetFamilies: ["terminal", "numeric-analysis", "model-evaluation"],
        risk: "medium",
      };
    case "exact-verifier-command":
      return {
        confidence: 0.8,
        summary: "Run the exact compiler, build, or verifier command expected downstream.",
        reusableBehavior:
          "When a task names or implies a build command, validate with that exact command and clean build inputs, not only with a nearby smoke test or already-built artifact.",
        targetFamilies: ["terminal", "build-validation", "artifact-contract"],
        risk: "low",
      };
    case "consumer-path-parity":
      return {
        confidence: 0.75,
        summary: "Validate through the same downstream consumer path that will be checked.",
        reusableBehavior:
          "For branch, deploy, push, clone, service, or protocol work, exercise the same downstream path and transport that the checker or consumer will use before marking the work done.",
        targetFamilies: ["terminal", "service-setup", "stateful-workflow"],
        risk: "medium",
      };
  }
}

function normalizeSignalText(input: string): string {
  return stripAnsi(input).toLowerCase().replace(/\s+/g, " ");
}

function roundConfidence(confidence: number): number {
  return Math.round(confidence * 100) / 100;
}

function collectEvidenceRefs(diagnostics: readonly ExternalEvalTrialDiagnostic[]): readonly string[] {
  return [...new Set(diagnostics.flatMap((diagnostic) => diagnostic.evidenceRefs))];
}

function validateCommand(input: unknown, errors: string[]): void {
  if (!isRecord(input)) {
    errors.push("command must be an object");
    return;
  }
  const argv = input.argv;
  if (!Array.isArray(argv) || !argv.every((part) => typeof part === "string" && part.length > 0)) {
    errors.push("command.argv must be an array of non-empty strings");
  }
  requireString(input, "cwd", errors, "command.cwd");
}

function validateArtifacts(input: unknown, errors: string[]): void {
  if (!isRecord(input)) {
    errors.push("artifacts must be an object");
    return;
  }
  requireString(input, "stdoutPath", errors, "artifacts.stdoutPath");
  requireString(input, "stderrPath", errors, "artifacts.stderrPath");
  requireOptionalString(input, "finalMessagePath", errors, "artifacts.finalMessagePath");

  if (Object.prototype.hasOwnProperty.call(input, "tokens")) {
    if (!isRecord(input.tokens)) {
      errors.push("artifacts.tokens must be an object");
    } else {
      requireNumber(input.tokens, "input", errors, "artifacts.tokens.input");
      requireNumber(input.tokens, "output", errors, "artifacts.tokens.output");
    }
  }
}

function requireString(
  input: Readonly<Record<string, unknown>>,
  field: string,
  errors: string[],
  label = field,
): void {
  if (typeof input[field] !== "string" || input[field].length === 0) {
    errors.push(`${label} must be a non-empty string`);
  }
}

function requireOptionalString(
  input: Readonly<Record<string, unknown>>,
  field: string,
  errors: string[],
  label = field,
): void {
  if (Object.prototype.hasOwnProperty.call(input, field) && typeof input[field] !== "string") {
    errors.push(`${label} must be a string when present`);
  }
}

function validateOptionalStringArray(
  input: Readonly<Record<string, unknown>>,
  field: string,
  errors: string[],
): void {
  if (!Object.prototype.hasOwnProperty.call(input, field)) return;
  const value = input[field];
  if (!Array.isArray(value) || !value.every((item) => typeof item === "string" && item.length > 0)) {
    errors.push(`${field} must be an array of non-empty strings when present`);
  }
}

function requireNumber(
  input: Readonly<Record<string, unknown>>,
  field: string,
  errors: string[],
  label = field,
): void {
  if (typeof input[field] !== "number" || !Number.isFinite(input[field]) || input[field] < 0) {
    errors.push(`${label} must be a non-negative finite number`);
  }
}

function requireOptionalNumber(
  input: Readonly<Record<string, unknown>>,
  field: string,
  errors: string[],
): void {
  if (
    Object.prototype.hasOwnProperty.call(input, field) &&
    (typeof input[field] !== "number" || !Number.isFinite(input[field]))
  ) {
    errors.push(`${field} must be a finite number when present`);
  }
}

function requireEnum(
  input: Readonly<Record<string, unknown>>,
  field: string,
  values: readonly string[],
  errors: string[],
): void {
  if (typeof input[field] !== "string" || !values.includes(input[field])) {
    errors.push(`${field} must be one of ${values.join(", ")}`);
  }
}

function isRecord(input: unknown): input is Readonly<Record<string, unknown>> {
  return typeof input === "object" && input !== null && !Array.isArray(input);
}
