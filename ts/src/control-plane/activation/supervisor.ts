import {
  RuntimeEffectExecutionMode,
  RuntimeEffectPolicy,
} from "../../runtimes/effect-policy.js";
import type {
  RuntimeActivationAuditEvent,
  RuntimeActivationFailureCode,
  RuntimeActivationJournalEntry,
  RuntimeActivationJournalOutcome,
  RuntimeActivationJournalRecord,
  RuntimeActivationOperation,
  RuntimeActivationRequest,
  RuntimeActivationResult,
  RuntimeActivationSession,
  RuntimeActivationStage,
  RuntimeActivationStatus,
  RuntimeActivationSupervisorOptions,
  RuntimeRollbackRequest,
} from "./types.js";

export class RuntimeActivationSupervisor {
  private readonly journal: RuntimeActivationSupervisorOptions["journal"];
  private readonly pointer: RuntimeActivationSupervisorOptions["pointer"];
  private readonly driver: RuntimeActivationSupervisorOptions["driver"];
  private readonly now: () => string;
  private readonly eventSink?: RuntimeActivationSupervisorOptions["eventSink"];
  private queue: Promise<unknown> = Promise.resolve();

  constructor(options: RuntimeActivationSupervisorOptions) {
    this.journal = options.journal;
    this.pointer = options.pointer;
    this.driver = options.driver;
    this.now = options.now ?? (() => new Date().toISOString());
    this.eventSink = options.eventSink;
  }

  activate(request: RuntimeActivationRequest): Promise<RuntimeActivationResult> {
    return this.exclusive(() => this.activateExclusive(request));
  }

  rollback(request: RuntimeRollbackRequest): Promise<RuntimeActivationResult> {
    return this.exclusive(() => this.rollbackExclusive(request));
  }

  recover(): Promise<RuntimeActivationStatus> {
    return this.exclusive(() => this.recoverExclusive());
  }

  /** Records that a higher-level metadata commit compensated this transaction. */
  markCompensated(
    transactionId: string,
    diverged = false,
  ): Promise<void> {
    return this.exclusive(async () => {
      const record = this.journal.load(transactionId);
      if (!record || record.operation !== "activate") {
        throw new Error("runtime activation transaction is unavailable for compensation");
      }
      this.finish(
        record,
        "failed",
        diverged ? "diverged" : "failed",
        "metadata_failed",
      );
    });
  }

  async status(): Promise<RuntimeActivationStatus> {
    const pointerArtifactId = this.pointer.read()?.artifactId ?? null;
    const observedArtifactId = await this.driver.observedArtifactId();
    const records = this.journal.list();
    return {
      pointerArtifactId,
      observedArtifactId,
      converged: pointerArtifactId === observedArtifactId,
      unfinishedTransactionIds: records
        .filter((record) => record.outcome === "in_progress")
        .map((record) => record.transactionId),
      divergentTransactionIds: records
        .filter((record) => record.outcome === "diverged")
        .map((record) => record.transactionId),
    };
  }

  private async activateExclusive(
    request: RuntimeActivationRequest,
  ): Promise<RuntimeActivationResult> {
    const existing = this.journal.load(request.transactionId);
    if (existing) {
      if (existing.operation !== "activate") {
        throw new Error("runtime activation transaction id belongs to another operation");
      }
      if (existing.outcome === "in_progress" || existing.outcome === "diverged") {
        await this.recoverRecord(existing);
      }
      return this.resultFor(this.journal.load(request.transactionId)!, true);
    }

    const priorArtifactId = this.pointer.read()?.artifactId ?? null;
    let record = this.createRecord({
      transactionId: request.transactionId,
      operation: "activate",
      candidateArtifactId: request.candidateArtifactId,
      priorArtifactId,
      targetMode: request.targetMode,
    });
    record = this.append(record, "staged", "succeeded");

    if (
      await this.driver.isActivated(request.candidateArtifactId, request.targetMode)
      && (
        request.targetMode !== "active"
        || priorArtifactId === request.candidateArtifactId
      )
    ) {
      record = this.finish(record, "committed", "succeeded");
      return this.resultFor(record, false);
    }

    let session: RuntimeActivationSession | undefined;
    let failureCode: RuntimeActivationFailureCode = "apply_failed";
    try {
      let effectPolicy: RuntimeEffectPolicy;
      try {
        effectPolicy = new RuntimeEffectPolicy({
          mode: stagingEffectMode(request.targetMode),
          untrustedComponent: request.untrustedComponent,
          sandbox: request.sandbox,
        });
      } catch {
        failureCode = "effect_policy_denied";
        throw new Error("effect policy denied candidate activation");
      }

      failureCode = "apply_failed";
      [record, session] = await this.startSession(record, request, priorArtifactId, effectPolicy);
      record = await this.runStage(record, "applying", "applied", () => session!.apply());

      failureCode = "validation_failed";
      record = await this.runStage(record, "validating", "validated", () => session!.validate());

      failureCode = "activation_failed";
      record = await this.runStage(record, "activating", "activated", () => session!.activate());

      failureCode = "drain_failed";
      record = await this.runStage(record, "draining", "drained", () => session!.drainPrior());

      failureCode = "cutover_failed";
      record = this.append(record, "cutting_over", "started");
      await session.cutover();
      if (!await this.driver.isActivated(request.candidateArtifactId, request.targetMode)) {
        failureCode = "observed_state_mismatch";
        throw new Error("runtime cutover did not produce the candidate");
      }
      record = this.append(record, "runtime_cutover", "succeeded");

      if (request.targetMode === "active") {
        failureCode = "pointer_failed";
        this.pointer.write({ artifactId: request.candidateArtifactId, asOf: this.now() });
        record = this.append(record, "pointer_cutover", "succeeded");

        failureCode = "disposal_failed";
        record = this.append(record, "disposing_prior", "started");
        await session.disposePrior();
      }
      record = this.finish(record, "committed", "succeeded");
      return this.resultFor(record, false);
    } catch (error) {
      if (isRuntimeActivationStageFailure(error)) {
        record = error.record;
      }
      record = this.append(record, record.stage, "failed", failureCode);
      record = await this.abortActivation(record, session, priorArtifactId, failureCode);
      return this.resultFor(record, false);
    }
  }

  private async rollbackExclusive(
    request: RuntimeRollbackRequest,
  ): Promise<RuntimeActivationResult> {
    const existing = this.journal.load(request.transactionId);
    if (existing) {
      if (existing.operation !== "rollback") {
        throw new Error("runtime activation transaction id belongs to another operation");
      }
      if (existing.outcome === "in_progress" || existing.outcome === "diverged") {
        await this.recoverRecord(existing);
      }
      return this.resultFor(this.journal.load(request.transactionId)!, true);
    }

    const candidateArtifactId = request.candidateArtifactId
      ?? this.pointer.read()?.artifactId
      ?? null;
    const activationRecord = candidateArtifactId === null
      ? undefined
      : [...this.journal.list()].reverse().find((candidate) =>
          candidate.operation === "activate"
          && candidate.candidateArtifactId === candidateArtifactId
          && (candidate.outcome === "succeeded" || candidate.outcome === "recovered"),
        );
    const targetMode = activationRecord?.targetMode ?? "active";
    let record = this.createRecord({
      transactionId: request.transactionId,
      operation: "rollback",
      candidateArtifactId,
      priorArtifactId: request.baselineArtifactId,
      targetMode,
    });
    record = this.append(record, "staged", "succeeded");

    if (
      !await this.driver.isActivated(candidateArtifactId ?? "", targetMode)
      && await this.driver.observedArtifactId() === request.baselineArtifactId
    ) {
      record = this.finish(record, "committed", "succeeded");
      return this.resultFor(record, false);
    }
    if (candidateArtifactId === null) {
      record = this.finish(record, "failed", "failed", "rollback_failed");
      return this.resultFor(record, false);
    }

    try {
      record = this.append(record, "reverting", "started");
      await this.driver.rollback({
        transactionId: request.transactionId,
        candidateArtifactId,
        baselineArtifactId: request.baselineArtifactId,
      });
      if (await this.driver.observedArtifactId() !== request.baselineArtifactId) {
        throw new Error("rollback did not restore the baseline");
      }
      record = this.append(record, "restored", "succeeded");
      if (targetMode === "active") {
        this.writePointerTarget(request.baselineArtifactId);
        record = this.append(record, "pointer_cutover", "succeeded");
      }
      record = this.finish(record, "committed", "succeeded");
    } catch {
      const pointerArtifactId = this.pointer.read()?.artifactId ?? null;
      const observedArtifactId = await this.driver.observedArtifactId();
      const candidateStillActivated = candidateArtifactId !== null
        && await this.driver.isActivated(candidateArtifactId, targetMode);
      const outcome = pointerArtifactId === observedArtifactId && !candidateStillActivated
        ? "failed"
        : "diverged";
      record = this.finish(record, "failed", outcome, "rollback_failed");
    }
    return this.resultFor(record, false);
  }

  private async recoverExclusive(): Promise<RuntimeActivationStatus> {
    for (const record of this.journal.list()) {
      if (record.outcome === "in_progress" || record.outcome === "diverged") {
        await this.recoverRecord(record);
      }
    }

    const pointerTarget = this.pointer.read()?.artifactId ?? null;
    if (await this.driver.observedArtifactId() !== pointerTarget) {
      await this.repairStaleRuntime(pointerTarget);
    }
    return this.status();
  }

  private async recoverRecord(initial: RuntimeActivationJournalRecord): Promise<void> {
    let record = initial;
    const restoreTarget = record.operation === "repair"
      ? record.candidateArtifactId
      : record.priorArtifactId;
    try {
      record = this.append(record, "reverting", "started");
      if (record.operation !== "repair" && record.candidateArtifactId !== null) {
        await this.driver.rollback({
          transactionId: record.transactionId,
          candidateArtifactId: record.candidateArtifactId,
          baselineArtifactId: restoreTarget,
        });
      } else {
        await this.driver.restore(restoreTarget);
      }
      if (await this.driver.observedArtifactId() !== restoreTarget) {
        throw new Error("runtime recovery did not restore the journal baseline");
      }
      this.writePointerTarget(restoreTarget);
      record = this.append(record, "restored", "succeeded");
      this.save({ ...record, outcome: "recovered", updatedAt: this.now() });
    } catch {
      this.finish(record, "failed", "diverged", "restore_failed");
    }
  }

  private async repairStaleRuntime(pointerTarget: string | null): Promise<void> {
    const transactionId = this.nextRepairTransactionId();
    let record = this.createRecord({
      transactionId,
      operation: "repair",
      candidateArtifactId: pointerTarget,
      priorArtifactId: await this.driver.observedArtifactId(),
      targetMode: "active",
    });
    record = this.append(record, "staged", "succeeded");
    try {
      record = this.append(record, "reverting", "started");
      await this.driver.restore(pointerTarget);
      if (await this.driver.observedArtifactId() !== pointerTarget) {
        throw new Error("stale runtime repair did not converge");
      }
      record = this.append(record, "restored", "succeeded");
      this.finish(record, "committed", "succeeded");
    } catch {
      this.finish(record, "failed", "diverged", "restore_failed");
    }
  }

  private async startSession(
    record: RuntimeActivationJournalRecord,
    request: RuntimeActivationRequest,
    priorArtifactId: string | null,
    effectPolicy: RuntimeEffectPolicy,
  ): Promise<[RuntimeActivationJournalRecord, RuntimeActivationSession]> {
    const session = await this.driver.beginActivation({
      transactionId: request.transactionId,
      candidateArtifactId: request.candidateArtifactId,
      priorArtifactId,
      targetMode: request.targetMode,
      effectPolicy,
    });
    return [record, session];
  }

  private async runStage(
    record: RuntimeActivationJournalRecord,
    started: RuntimeActivationStage,
    succeeded: RuntimeActivationStage,
    action: () => Promise<void>,
  ): Promise<RuntimeActivationJournalRecord> {
    let next = this.append(record, started, "started");
    try {
      await action();
    } catch {
      throw new RuntimeActivationStageFailure(next);
    }
    next = this.append(next, succeeded, "succeeded");
    return next;
  }

  private async abortActivation(
    record: RuntimeActivationJournalRecord,
    session: RuntimeActivationSession | undefined,
    priorArtifactId: string | null,
    originalFailure: RuntimeActivationFailureCode,
  ): Promise<RuntimeActivationJournalRecord> {
    try {
      record = this.append(record, "reverting", "started");
      if (session) {
        await session.abort();
      } else {
        await this.driver.restore(priorArtifactId);
      }
      if (await this.driver.observedArtifactId() !== priorArtifactId) {
        throw new Error("candidate abort did not restore the prior runtime");
      }
      this.writePointerTarget(priorArtifactId);
      record = this.append(record, "restored", "succeeded");
      return this.finish(record, "failed", "failed", originalFailure);
    } catch {
      return this.finish(record, "failed", "diverged", "restore_failed");
    }
  }

  private createRecord(input: {
    transactionId: string;
    operation: RuntimeActivationOperation;
    candidateArtifactId: string | null;
    priorArtifactId: string | null;
    targetMode: RuntimeActivationJournalRecord["targetMode"];
  }): RuntimeActivationJournalRecord {
    const now = this.now();
    const record: RuntimeActivationJournalRecord = {
      schemaVersion: 1,
      ...input,
      stage: "staged",
      outcome: "in_progress",
      entries: [],
      createdAt: now,
      updatedAt: now,
    };
    this.save(record);
    return record;
  }

  private append(
    record: RuntimeActivationJournalRecord,
    stage: RuntimeActivationStage,
    entryOutcome: RuntimeActivationJournalEntry["outcome"],
    failureCode?: RuntimeActivationFailureCode,
  ): RuntimeActivationJournalRecord {
    const entry: RuntimeActivationJournalEntry = {
      sequence: record.entries.length,
      stage,
      outcome: entryOutcome,
      timestamp: this.now(),
      ...(failureCode ? { failureCode } : {}),
    };
    const next: RuntimeActivationJournalRecord = {
      ...record,
      stage,
      ...(failureCode ? { failureCode } : {}),
      entries: [...record.entries, entry],
      updatedAt: entry.timestamp,
    };
    this.save(next);
    return next;
  }

  private finish(
    record: RuntimeActivationJournalRecord,
    stage: RuntimeActivationStage,
    outcome: RuntimeActivationJournalOutcome,
    failureCode?: RuntimeActivationFailureCode,
  ): RuntimeActivationJournalRecord {
    let next = record;
    if (record.stage !== stage || record.entries.at(-1)?.outcome !== "succeeded") {
      next = this.append(record, stage, outcome === "succeeded" ? "succeeded" : "failed", failureCode);
    }
    next = {
      ...next,
      stage,
      outcome,
      ...(failureCode ? { failureCode } : {}),
      updatedAt: this.now(),
    };
    this.save(next);
    return next;
  }

  private save(record: RuntimeActivationJournalRecord): void {
    this.journal.save(record);
    this.emit(record);
  }

  private emit(record: RuntimeActivationJournalRecord): void {
    const event: RuntimeActivationAuditEvent = {
      transactionId: record.transactionId,
      operation: record.operation,
      candidateArtifactId: record.candidateArtifactId,
      priorArtifactId: record.priorArtifactId,
      stage: record.stage,
      outcome: record.outcome,
      failureCode: record.failureCode,
    };
    try {
      this.eventSink?.onRuntimeActivationAuditEvent(event);
    } catch {
      // Durable state transitions do not depend on observer availability.
    }
  }

  private writePointerTarget(artifactId: string | null): void {
    if (artifactId === null) {
      this.pointer.clear();
    } else {
      this.pointer.write({ artifactId, asOf: this.now() });
    }
  }

  private async resultFor(
    record: RuntimeActivationJournalRecord,
    idempotentReplay: boolean,
  ): Promise<RuntimeActivationResult> {
    const outcome = record.outcome === "in_progress" || record.outcome === "diverged"
      ? "failed"
      : record.outcome;
    return {
      transactionId: record.transactionId,
      operation: record.operation,
      outcome,
      activeArtifactId: await this.driver.observedArtifactId(),
      failureCode: record.failureCode,
      idempotentReplay,
    };
  }

  private nextRepairTransactionId(): string {
    const base = `repair-${this.now().replace(/[^A-Za-z0-9._-]/g, "-")}`;
    let candidate = base;
    let suffix = 1;
    while (this.journal.load(candidate)) {
      candidate = `${base}-${suffix}`;
      suffix += 1;
    }
    return candidate;
  }

  private exclusive<T>(operation: () => Promise<T>): Promise<T> {
    const result = this.queue.then(operation, operation);
    this.queue = result.catch(() => undefined);
    return result;
  }
}

class RuntimeActivationStageFailure extends Error {
  readonly record: RuntimeActivationJournalRecord;

  constructor(record: RuntimeActivationJournalRecord) {
    super("runtime activation stage failed");
    this.record = record;
  }
}

function isRuntimeActivationStageFailure(error: unknown): error is RuntimeActivationStageFailure {
  return error instanceof RuntimeActivationStageFailure;
}

function stagingEffectMode(
  targetMode: RuntimeActivationRequest["targetMode"],
): typeof RuntimeEffectExecutionMode[keyof typeof RuntimeEffectExecutionMode] {
  if (targetMode === "shadow") return RuntimeEffectExecutionMode.SHADOW;
  if (targetMode === "canary") return RuntimeEffectExecutionMode.CANARY;
  return RuntimeEffectExecutionMode.CANDIDATE;
}
