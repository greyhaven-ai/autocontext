/**
 * Mission manager — lifecycle orchestration (AC-410).
 *
 * Create, advance, verify, pause, resume, cancel missions.
 * Verifier-driven completion: mission completes only when
 * an external verifier confirms success.
 */

import { MissionStore } from "./store.js";
import { saveCheckpoint } from "./checkpoint.js";
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
    if (!verifier) {
      const result: VerifierResult = { passed: false, reason: "No verifier registered", suggestions: [], metadata: {} };
      this.store.recordVerification(missionId, result);
      this.events?.emitVerified(missionId, result.passed, result.reason);
      return result;
    }

    try {
      const result = await verifier(missionId);
      this.store.recordVerification(missionId, result);
      this.events?.emitVerified(missionId, result.passed, result.reason);

      if (result.passed) {
        this.transitionMissionStatus(missionId, "completed");
      }

      return result;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const result: VerifierResult = {
        passed: false,
        reason: `Verifier error: ${message}`,
        suggestions: [],
        metadata: {
          verifierThrew: true,
          errorName: error instanceof Error ? error.name : "Error",
        },
      };
      this.store.recordVerification(missionId, result);
      this.events?.emitVerified(missionId, result.passed, result.reason);
      return result;
    }
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

  private transitionMissionStatus(missionId: string, status: MissionStatus): void {
    const mission = this.store.getMission(missionId);
    const previousStatus = mission?.status;
    this.store.updateMissionStatus(missionId, status);
    if (previousStatus && previousStatus !== status) {
      this.events?.emitStatusChange(missionId, previousStatus, status);
    }
  }

  close(): void {
    this.store.close();
  }
}
