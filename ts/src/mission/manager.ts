/**
 * Mission manager — lifecycle orchestration (AC-410).
 *
 * Create, advance, verify, pause, resume, cancel missions.
 * Verifier-driven completion: mission completes only when
 * an external verifier confirms success.
 */

import { MissionStore } from "./store.js";
import type { Mission, MissionBudget, MissionStatus, MissionStep, MissionVerifier, VerifierResult } from "./types.js";

export class MissionManager {
  private store: MissionStore;
  private verifiers: Map<string, MissionVerifier> = new Map();

  constructor(dbPath: string) {
    this.store = new MissionStore(dbPath);
  }

  create(opts: { name: string; goal: string; budget?: MissionBudget; metadata?: Record<string, unknown> }): string {
    return this.store.createMission(opts);
  }

  get(id: string): Mission | null {
    return this.store.getMission(id);
  }

  list(status?: MissionStatus): Mission[] {
    return this.store.listMissions(status);
  }

  advance(missionId: string, description: string): string {
    return this.store.addStep(missionId, { description });
  }

  steps(missionId: string): MissionStep[] {
    return this.store.getSteps(missionId);
  }

  setVerifier(missionId: string, verifier: MissionVerifier): void {
    this.verifiers.set(missionId, verifier);
  }

  async verify(missionId: string): Promise<VerifierResult> {
    const verifier = this.verifiers.get(missionId);
    if (!verifier) {
      const result: VerifierResult = { passed: false, reason: "No verifier registered", suggestions: [], metadata: {} };
      this.store.recordVerification(missionId, result);
      return result;
    }

    try {
      const result = await verifier(missionId);
      this.store.recordVerification(missionId, result);

      if (result.passed) {
        this.store.updateMissionStatus(missionId, "completed");
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
      return result;
    }
  }

  pause(missionId: string): void {
    this.store.updateMissionStatus(missionId, "paused");
  }

  resume(missionId: string): void {
    this.store.updateMissionStatus(missionId, "active");
  }

  cancel(missionId: string): void {
    this.store.updateMissionStatus(missionId, "canceled");
  }

  close(): void {
    this.store.close();
  }
}
