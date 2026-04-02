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

const ALIVE_STATES = new Set<SupervisorState>([
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

  markRunning(): void {
    this.requireState(
      new Set([SupervisorState.LAUNCHING, SupervisorState.WAITING]),
      "mark entry running",
    );
    this.state = SupervisorState.RUNNING;
    this.blockedReason = "";
    this.touch();
  }

  markWaiting(reason: string = ""): void {
    this.requireState(
      new Set([SupervisorState.LAUNCHING, SupervisorState.RUNNING]),
      "mark entry waiting",
    );
    this.state = SupervisorState.WAITING;
    this.blockedReason = reason;
    this.touch();
  }

  markCompleted(): void {
    this.requireState(
      new Set([
        SupervisorState.LAUNCHING,
        SupervisorState.RUNNING,
        SupervisorState.WAITING,
        SupervisorState.STOPPING,
      ]),
      "mark entry completed",
    );
    this.state = SupervisorState.COMPLETED;
    this.blockedReason = "";
    this.touch();
  }

  markFailed(error: string = ""): void {
    this.requireState(ALIVE_STATES, "mark entry failed");
    this.state = SupervisorState.FAILED;
    this.blockedReason = "";
    this.error = error;
    this.touch();
  }

  requestStop(): void {
    this.requireState(
      new Set([SupervisorState.LAUNCHING, SupervisorState.RUNNING, SupervisorState.WAITING]),
      "request stop for entry",
    );
    this.state = SupervisorState.STOPPING;
    this.blockedReason = "";
    this.touch();
  }

  markStopped(): void {
    this.requireState(new Set([SupervisorState.STOPPING]), "mark entry stopped");
    this.state = SupervisorState.STOPPED;
    this.blockedReason = "";
    this.touch();
  }

  heartbeat(): void { this.touch(); }

  get isAlive(): boolean { return ALIVE_STATES.has(this.state); }

  private requireState(allowed: Set<SupervisorState>, action: string): void {
    if (!allowed.has(this.state)) {
      throw new Error(`Cannot ${action} from state=${this.state}`);
    }
  }

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
