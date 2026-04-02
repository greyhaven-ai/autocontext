/**
 * Session supervisor — background work registry (AC-510 TS parity).
 *
 * Port of Python autocontext.session.supervisor.
 */

import { randomUUID } from "node:crypto";

export const SupervisorState = {
  LAUNCHING: "launching",
  RUNNING: "running",
  WAITING: "waiting",
  STOPPING: "stopping",
  STOPPED: "stopped",
  COMPLETED: "completed",
  FAILED: "failed",
} as const;
export type SupervisorState = (typeof SupervisorState)[keyof typeof SupervisorState];

const ALIVE_STATES = new Set<string>([
  SupervisorState.LAUNCHING,
  SupervisorState.RUNNING,
  SupervisorState.WAITING,
  SupervisorState.STOPPING,
]);

export class SupervisedEntry {
  readonly entryId: string;
  readonly sessionId: string;
  readonly goal: string;
  readonly workspace: string;
  state: SupervisorState = SupervisorState.LAUNCHING;
  blockedReason: string = "";
  error: string = "";
  lastActivityAt: string;

  private constructor(opts: { sessionId: string; goal: string; workspace?: string }) {
    this.entryId = randomUUID().slice(0, 12);
    this.sessionId = opts.sessionId;
    this.goal = opts.goal;
    this.workspace = opts.workspace ?? "";
    this.lastActivityAt = new Date().toISOString();
  }

  static create(opts: { sessionId: string; goal: string; workspace?: string }): SupervisedEntry {
    return new SupervisedEntry(opts);
  }

  markRunning(): void { this.state = SupervisorState.RUNNING; this.blockedReason = ""; this.touch(); }
  markWaiting(reason: string = ""): void { this.state = SupervisorState.WAITING; this.blockedReason = reason; this.touch(); }
  markCompleted(): void { this.state = SupervisorState.COMPLETED; this.touch(); }
  markFailed(error: string = ""): void { this.state = SupervisorState.FAILED; this.error = error; this.touch(); }
  requestStop(): void { this.state = SupervisorState.STOPPING; this.touch(); }
  markStopped(): void { this.state = SupervisorState.STOPPED; this.touch(); }
  heartbeat(): void { this.touch(); }

  get isAlive(): boolean { return ALIVE_STATES.has(this.state); }

  private touch(): void { this.lastActivityAt = new Date().toISOString(); }
}

export class Supervisor {
  private entries = new Map<string, SupervisedEntry>();

  launch(opts: { sessionId: string; goal: string; workspace?: string }): SupervisedEntry {
    if (this.entries.has(opts.sessionId)) {
      throw new Error(`Session '${opts.sessionId}' is already supervised`);
    }
    const entry = SupervisedEntry.create(opts);
    this.entries.set(opts.sessionId, entry);
    return entry;
  }

  get(sessionId: string): SupervisedEntry | undefined {
    return this.entries.get(sessionId);
  }

  listActive(): SupervisedEntry[] {
    return [...this.entries.values()].filter((e) => e.isAlive);
  }

  listAll(): SupervisedEntry[] {
    return [...this.entries.values()];
  }

  stop(sessionId: string): void {
    const entry = this.entries.get(sessionId);
    if (!entry) throw new Error(`Session '${sessionId}' not found in supervisor`);
    entry.requestStop();
  }

  remove(sessionId: string): boolean {
    return this.entries.delete(sessionId);
  }
}
