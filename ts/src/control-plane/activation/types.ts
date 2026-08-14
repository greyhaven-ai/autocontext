import type {
  RuntimeEffectPolicy,
  RuntimeEffectSandboxPolicy,
} from "../../runtimes/effect-policy.js";

export type RuntimeActivationOperation = "activate" | "rollback" | "repair";

export type RuntimeActivationStage =
  | "staged"
  | "applying"
  | "applied"
  | "validating"
  | "validated"
  | "activating"
  | "activated"
  | "draining"
  | "drained"
  | "cutting_over"
  | "runtime_cutover"
  | "pointer_cutover"
  | "disposing_prior"
  | "reverting"
  | "restored"
  | "committed"
  | "failed";

export type RuntimeActivationJournalOutcome =
  | "in_progress"
  | "succeeded"
  | "failed"
  | "recovered"
  | "diverged";

export type RuntimeActivationFailureCode =
  | "apply_failed"
  | "validation_failed"
  | "activation_failed"
  | "drain_failed"
  | "cutover_failed"
  | "pointer_failed"
  | "disposal_failed"
  | "rollback_failed"
  | "restore_failed"
  | "effect_policy_denied"
  | "metadata_failed"
  | "observed_state_mismatch";

export interface RuntimeActivationJournalEntry {
  readonly sequence: number;
  readonly stage: RuntimeActivationStage;
  readonly outcome: "started" | "succeeded" | "failed";
  readonly timestamp: string;
  readonly failureCode?: RuntimeActivationFailureCode;
}

export interface RuntimeActivationJournalRecord {
  readonly schemaVersion: 1;
  readonly transactionId: string;
  readonly operation: RuntimeActivationOperation;
  readonly candidateArtifactId: string | null;
  readonly priorArtifactId: string | null;
  readonly targetMode: RuntimeActivationTargetMode;
  readonly stage: RuntimeActivationStage;
  readonly outcome: RuntimeActivationJournalOutcome;
  readonly failureCode?: RuntimeActivationFailureCode;
  readonly entries: readonly RuntimeActivationJournalEntry[];
  readonly createdAt: string;
  readonly updatedAt: string;
}

export interface RuntimeActivationJournalStore {
  load(transactionId: string): RuntimeActivationJournalRecord | null;
  save(record: RuntimeActivationJournalRecord): void;
  list(): RuntimeActivationJournalRecord[];
}

export interface RuntimeActivationPointer {
  readonly artifactId: string;
  readonly asOf: string;
}

export interface RuntimeActivationPointerStore {
  read(): RuntimeActivationPointer | null;
  write(pointer: RuntimeActivationPointer): void;
  clear(): void;
}

export type RuntimeActivationTargetMode = "candidate" | "shadow" | "canary" | "active";

export interface RuntimeActivationSessionInput {
  readonly transactionId: string;
  readonly candidateArtifactId: string;
  readonly priorArtifactId: string | null;
  readonly targetMode: RuntimeActivationTargetMode;
  readonly effectPolicy: RuntimeEffectPolicy;
}

export interface RuntimeActivationSession {
  apply(): Promise<void>;
  validate(): Promise<void>;
  activate(): Promise<void>;
  drainPrior(): Promise<void>;
  cutover(): Promise<void>;
  disposePrior(): Promise<void>;
  /** Restore the prior live runtime and inverse any staged candidate effects. */
  abort(): Promise<void>;
}

export interface RuntimeActivationDriver {
  beginActivation(input: RuntimeActivationSessionInput): Promise<RuntimeActivationSession>;
  rollback(input: {
    transactionId: string;
    candidateArtifactId: string;
    baselineArtifactId: string | null;
  }): Promise<void>;
  restore(artifactId: string | null): Promise<void>;
  observedArtifactId(): Promise<string | null> | string | null;
  isActivated(
    artifactId: string,
    mode: RuntimeActivationTargetMode,
  ): Promise<boolean> | boolean;
}

export interface RuntimeActivationRequest {
  readonly transactionId: string;
  readonly candidateArtifactId: string;
  readonly targetMode: RuntimeActivationTargetMode;
  readonly untrustedComponent?: boolean;
  readonly sandbox?: RuntimeEffectSandboxPolicy;
}

export interface RuntimeRollbackRequest {
  readonly transactionId: string;
  /** Defaults to the active pointer; required to remove a shadow/canary deployment. */
  readonly candidateArtifactId?: string;
  readonly baselineArtifactId: string | null;
}

export interface RuntimeActivationResult {
  readonly transactionId: string;
  readonly operation: RuntimeActivationOperation;
  readonly outcome: Exclude<RuntimeActivationJournalOutcome, "in_progress" | "diverged">;
  readonly activeArtifactId: string | null;
  readonly failureCode?: RuntimeActivationFailureCode;
  readonly idempotentReplay: boolean;
}

export interface RuntimeActivationStatus {
  readonly pointerArtifactId: string | null;
  readonly observedArtifactId: string | null;
  readonly converged: boolean;
  readonly unfinishedTransactionIds: readonly string[];
  readonly divergentTransactionIds: readonly string[];
}

export interface RuntimeActivationAuditEvent {
  readonly transactionId: string;
  readonly operation: RuntimeActivationOperation;
  readonly candidateArtifactId: string | null;
  readonly priorArtifactId: string | null;
  readonly stage: RuntimeActivationStage;
  readonly outcome: RuntimeActivationJournalOutcome | "started";
  readonly failureCode?: RuntimeActivationFailureCode;
}

export interface RuntimeActivationAuditEventSink {
  onRuntimeActivationAuditEvent(event: RuntimeActivationAuditEvent): void;
}

export interface RuntimeActivationSupervisorOptions {
  readonly journal: RuntimeActivationJournalStore;
  readonly pointer: RuntimeActivationPointerStore;
  readonly driver: RuntimeActivationDriver;
  readonly now?: () => string;
  readonly eventSink?: RuntimeActivationAuditEventSink;
}
