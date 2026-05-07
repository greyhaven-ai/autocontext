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

export type ExternalEvalDiagnosticCategory =
  | "agent-task-failure"
  | "verifier-contract-mismatch"
  | "setup-environment-failure"
  | "adapter-runtime-failure"
  | "integrity-risk"
  | "unknown";

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
}

export interface ExternalEvalTrialEvidence {
  readonly trialId: string;
  readonly evidenceRefs?: readonly string[];
  readonly verifierOutput?: string;
  readonly adapterLifecycle?: ExternalEvalAdapterLifecycle;
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

export interface ExternalEvalDiagnosticReport {
  readonly schemaVersion: "external-eval-diagnostics/v1";
  readonly runId: string;
  readonly createdAt: string;
  readonly diagnostics: readonly ExternalEvalTrialDiagnostic[];
  readonly summary: {
    readonly totalTrials: number;
    readonly unresolvedTrials: number;
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

export function classifyExternalEvalTrial(inputs: ClassifyExternalEvalTrialInputs): EvalTrial {
  const status = inputs.isResolved ? "passed" : classifyUnresolvedStatus(inputs);
  const errorKind = classifyErrorKind(inputs, status);
  const reward = inputs.reward ?? defaultReward(status);
  const notes = buildTrialNotes(inputs);

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

export function buildExternalEvalDiagnosticReport(
  inputs: BuildExternalEvalDiagnosticReportInputs,
): ExternalEvalDiagnosticReport {
  const evidenceByTrialId = new Map<string, ExternalEvalTrialEvidence>();
  for (const evidence of inputs.evidence ?? []) {
    evidenceByTrialId.set(evidence.trialId, evidence);
  }

  const diagnostics = inputs.trials
    .filter((trial) => trial.status !== "passed")
    .map((trial) => buildTrialDiagnostic(inputs.runId, trial, evidenceByTrialId.get(trial.trialId)));

  return {
    schemaVersion: "external-eval-diagnostics/v1",
    runId: inputs.runId,
    createdAt: inputs.createdAt,
    diagnostics,
    summary: {
      totalTrials: inputs.trials.length,
      unresolvedTrials: diagnostics.length,
      countsByCategory: countDiagnosticsByCategory(diagnostics),
    },
  };
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
  if (status === "passed" || status === "failed") {
    return normalizedFailureMode(inputs.failureMode) || undefined;
  }
  const failureMode = normalizedFailureMode(inputs.failureMode);
  return failureMode || inputs.lifecycle?.errorKind || inputs.lifecycle?.timeoutSource || inputs.lifecycle?.status;
}

function normalizedFailureMode(failureMode: string | undefined): string {
  return failureMode === undefined || failureMode === "unset" ? "" : failureMode;
}

function defaultReward(status: EvalTrialStatus): number | undefined {
  if (status === "passed") return 1;
  if (status === "failed") return 0;
  return undefined;
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
  if (trial.status === "discarded") {
    return "integrity-risk";
  }
  if (
    trial.status === "infrastructure-error" ||
    evidence?.adapterLifecycle?.status === "timed-out" ||
    evidence?.adapterLifecycle?.status === "failed" ||
    isInfrastructureFailureMode(trial.errorKind) ||
    isInfrastructureFailureMode(evidence?.adapterLifecycle?.errorKind)
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
  if (category === "adapter-runtime-failure" && trial.status === "infrastructure-error") return 0.95;
  if (category === "integrity-risk" && trial.status === "discarded") return 0.9;
  if (evidence?.verifierOutput !== undefined && evidence.verifierOutput.length > 0) return 0.85;
  return category === "unknown" ? 0.25 : 0.6;
}

function diagnosticSummary(category: ExternalEvalDiagnosticCategory): string {
  switch (category) {
    case "adapter-runtime-failure":
      return "The trial failed in the adapter or runtime before an ordinary verifier result could be trusted.";
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
  if (category === "adapter-runtime-failure" && evidence?.adapterLifecycle !== undefined) {
    const lifecycle = evidence.adapterLifecycle;
    return [
      `adapter_status=${lifecycle.status}`,
      ...(lifecycle.errorKind !== undefined ? [`adapter_error_kind=${lifecycle.errorKind}`] : []),
      ...(lifecycle.timeoutSource !== undefined ? [`timeout_source=${lifecycle.timeoutSource}`] : []),
    ];
  }
  if (category === "integrity-risk") {
    return [
      `trial_status=${trial.status}`,
      ...(trial.errorKind !== undefined ? [`error_kind=${trial.errorKind}`] : []),
      ...(trial.notes ?? []),
    ];
  }

  return sanitizeVerifierOutput(evidence?.verifierOutput ?? "");
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

function sanitizeVerifierOutput(output: string): readonly string[] {
  const excerpts: string[] = [];
  for (const rawLine of stripAnsi(output).split(/\r?\n/)) {
    const line = sanitizeVerifierLine(rawLine);
    if (line.length === 0 || excerpts.includes(line)) continue;
    excerpts.push(line);
    if (excerpts.length >= 4) break;
  }
  return excerpts;
}

function sanitizeVerifierLine(rawLine: string): string {
  const line = rawLine.trim();
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

  return findings;
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
