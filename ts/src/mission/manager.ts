/**
 * Mission manager — lifecycle orchestration (AC-410).
 *
 * Create, advance, verify, pause, resume, cancel missions.
 * Verifier-driven completion: mission completes only when
 * an external verifier confirms success.
 */

import { MissionStore } from "./store.js";
import { saveCheckpoint } from "./checkpoint.js";
import { resolveMissionStatusTransition } from "./lifecycle.js";
import {
  buildMissingVerifierOutcome,
  resolveMissionVerificationErrorOutcome,
  resolveMissionVerificationOutcome,
} from "./verification-workflow.js";
import type { MissionEventEmitter } from "./events.js";
import type { Mission, MissionBudget, MissionStatus, MissionStep, MissionSubgoal, MissionVerifier, VerifierResult } from "./types.js";

export class MissionManager {
  private store: MissionStore;
  private verifiers: Map<string, MissionVerifier> = new Map();
  private events?: MissionEventEmitter;

  constructor(dbPath: string, opts?: { events?: MissionEventEmitter }) {
    this.store = new MissionStore(dbPath);
    this.events = opts?.events;
  }

  create(opts: { name: string; goal: string; budget?: MissionBudget; metadata?: Record<string, unknown> }): string {
    const id = this.store.createMission(opts);
    this.events?.emitCreated(id, opts.name, opts.goal);
    return id;
  }

  get(id: string): Mission | null {
    return this.store.getMission(id);
  }

  list(status?: MissionStatus): Mission[] {
    return this.store.listMissions(status);
  }

  advance(missionId: string, description: string): string {
    const stepId = this.store.addStep(missionId, { description });
    this.events?.emitStep(missionId, description, this.store.getSteps(missionId).length);
    return stepId;
  }

  steps(missionId: string): MissionStep[] {
    return this.store.getSteps(missionId);
  }

  subgoals(missionId: string): MissionSubgoal[] {
    return this.store.getSubgoals(missionId);
  }

  verifications(missionId: string) {
    return this.store.getVerifications(missionId);
  }

  setVerifier(missionId: string, verifier: MissionVerifier): void {
    this.verifiers.set(missionId, verifier);
  }

  hasVerifier(missionId: string): boolean {
    return this.verifiers.has(missionId);
  }

  async verify(missionId: string): Promise<VerifierResult> {
    const verifier = this.verifiers.get(missionId);
    const outcome = !verifier
      ? buildMissingVerifierOutcome()
      : await this.#runVerifierWorkflow(missionId, verifier);

    this.store.recordVerification(missionId, outcome.result);
    this.events?.emitVerified(missionId, outcome.result.passed, outcome.result.reason);

    if (outcome.nextStatus) {
      this.transitionMissionStatus(missionId, outcome.nextStatus);
    }

    return outcome.result;
  }

  pause(missionId: string): void {
    this.transitionMissionStatus(missionId, "paused");
  }

  resume(missionId: string): void {
    this.transitionMissionStatus(missionId, "active");
  }

  cancel(missionId: string): void {
    this.transitionMissionStatus(missionId, "canceled");
  }

  setStatus(missionId: string, status: MissionStatus): void {
    this.transitionMissionStatus(missionId, status);
  }

  budgetUsage(missionId: string): { stepsUsed: number; maxSteps?: number; exhausted: boolean } {
    return this.store.getBudgetUsage(missionId);
  }

  getDbPath(): string {
    return this.store.getDbPath();
  }

  updateStep(stepId: string, status: "completed" | "failed" | "blocked", result?: string): void {
    this.store.updateStepStatus(stepId, status, result);
  }

  addSubgoal(missionId: string, opts: { description: string; priority?: number }): string {
    return this.store.addSubgoal(missionId, opts);
  }

  updateSubgoalStatus(subgoalId: string, status: "pending" | "active" | "completed" | "failed" | "skipped"): void {
    this.store.updateSubgoalStatus(subgoalId, status);
  }

  saveCheckpoint(missionId: string, checkpointDir: string): string {
    return saveCheckpoint(this.store, missionId, checkpointDir);
  }

  async #runVerifierWorkflow(
    missionId: string,
    verifier: MissionVerifier,
  ): Promise<ReturnType<typeof resolveMissionVerificationOutcome>> {
    try {
      return resolveMissionVerificationOutcome(await verifier(missionId));
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      return resolveMissionVerificationErrorOutcome(
        message,
        error instanceof Error ? error.name : "Error",
      );
    }
  }

  private transitionMissionStatus(missionId: string, status: MissionStatus): void {
    const mission = this.store.getMission(missionId);
    const previousStatus = mission?.status;
    const transition = resolveMissionStatusTransition(previousStatus, status);
    this.store.updateMissionStatus(missionId, transition.nextStatus);
    if (previousStatus && transition.shouldEmitStatusChange) {
      this.events?.emitStatusChange(missionId, previousStatus, transition.nextStatus);
    }
  }

  close(): void {
    this.store.close();
  }
}
