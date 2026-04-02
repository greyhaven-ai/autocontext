/**
 * Coordinator-first multi-worker execution (AC-515 TS parity).
 *
 * Port of Python autocontext.session.coordinator.
 */

import { randomUUID } from "node:crypto";

export const WorkerStatus = {
  PENDING: "pending",
  RUNNING: "running",
  COMPLETED: "completed",
  FAILED: "failed",
  REDIRECTED: "redirected",
} as const;
export type WorkerStatus = (typeof WorkerStatus)[keyof typeof WorkerStatus];

export const CoordinatorEventType = {
  COORDINATOR_CREATED: "coordinator_created",
  WORKER_DELEGATED: "worker_delegated",
  WORKER_STARTED: "worker_started",
  WORKER_COMPLETED: "worker_completed",
  WORKER_FAILED: "worker_failed",
  WORKER_REDIRECTED: "worker_redirected",
  FAN_OUT: "fan_out",
  FAN_IN: "fan_in",
} as const;
export type CoordinatorEventType = (typeof CoordinatorEventType)[keyof typeof CoordinatorEventType];

const ACTIVE_STATUSES = new Set<WorkerStatus>([WorkerStatus.PENDING, WorkerStatus.RUNNING]);
const RETRYABLE_STATUSES = new Set<WorkerStatus>([WorkerStatus.FAILED, WorkerStatus.REDIRECTED]);

export interface CoordinatorEvent {
  readonly eventId: string;
  readonly eventType: CoordinatorEventType;
  readonly timestamp: string;
  readonly payload: Record<string, unknown>;
}

export class Worker {
  readonly workerId: string;
  readonly task: string;
  readonly role: string;
  status: WorkerStatus = WorkerStatus.PENDING;
  result: string = "";
  error: string = "";
  redirectReason: string = "";
  readonly parentWorkerId: string;

  private constructor(opts: { task: string; role: string; parentWorkerId?: string }) {
    this.workerId = randomUUID().slice(0, 12);
    this.task = opts.task;
    this.role = opts.role;
    this.parentWorkerId = opts.parentWorkerId ?? "";
  }

  static create(opts: { task: string; role: string; parentWorkerId?: string }): Worker {
    return new Worker(opts);
  }

  start(): void {
    this.requireStatus(new Set([WorkerStatus.PENDING]), "start worker");
    this.status = WorkerStatus.RUNNING;
  }

  complete(result: string): void {
    this.requireStatus(new Set([WorkerStatus.RUNNING]), "complete worker");
    this.status = WorkerStatus.COMPLETED;
    this.result = result;
  }

  fail(error: string = ""): void {
    this.requireStatus(new Set([WorkerStatus.RUNNING]), "fail worker");
    this.status = WorkerStatus.FAILED;
    this.error = error;
  }

  redirect(reason: string = ""): void {
    this.requireStatus(new Set([WorkerStatus.RUNNING]), "redirect worker");
    this.status = WorkerStatus.REDIRECTED;
    this.redirectReason = reason;
  }

  get isActive(): boolean { return ACTIVE_STATUSES.has(this.status); }

  private requireStatus(allowed: Set<WorkerStatus>, action: string): void {
    if (!allowed.has(this.status)) {
      throw new Error(`Cannot ${action} from status=${this.status}`);
    }
  }
}

export class Coordinator {
  readonly coordinatorId: string;
  readonly sessionId: string;
  readonly goal: string;
  readonly workers: Worker[] = [];
  readonly events: CoordinatorEvent[] = [];

  private constructor(sessionId: string, goal: string) {
    this.coordinatorId = randomUUID().slice(0, 12);
    this.sessionId = sessionId;
    this.goal = goal;
  }

  static create(sessionId: string, goal: string): Coordinator {
    const coord = new Coordinator(sessionId, goal);
    coord.emit(CoordinatorEventType.COORDINATOR_CREATED, { goal });
    return coord;
  }

  delegate(task: string, role: string, parentWorkerId?: string): Worker {
    const worker = Worker.create({ task, role, parentWorkerId });
    this.workers.push(worker);
    this.emit(CoordinatorEventType.WORKER_DELEGATED, { workerId: worker.workerId, task, role });
    return worker;
  }

  fanOut(tasks: Array<{ task: string; role: string }>): Worker[] {
    const workers = tasks.map((t) => this.delegate(t.task, t.role));
    this.emit(CoordinatorEventType.FAN_OUT, { count: workers.length });
    return workers;
  }

  fanIn(): string[] {
    const results = this.workers
      .filter((w) => w.status === WorkerStatus.COMPLETED)
      .map((w) => w.result);
    this.emit(CoordinatorEventType.FAN_IN, { resultCount: results.length });
    return results;
  }

  completeWorker(workerId: string, result: string): void {
    this.getWorker(workerId).complete(result);
    this.emit(CoordinatorEventType.WORKER_COMPLETED, { workerId });
  }

  stopWorker(workerId: string, reason: string = ""): void {
    this.getWorker(workerId).redirect(reason);
    this.emit(CoordinatorEventType.WORKER_REDIRECTED, { workerId, reason });
  }

  retry(workerId: string, newTask?: string): Worker {
    const parent = this.getWorker(workerId);
    if (!RETRYABLE_STATUSES.has(parent.status)) {
      throw new Error(
        `Cannot retry worker unless it is failed or redirected (status=${parent.status})`,
      );
    }
    return this.delegate(newTask ?? parent.task, parent.role, parent.workerId);
  }

  get activeWorkers(): Worker[] {
    return this.workers.filter((w) => w.isActive);
  }

  private getWorker(workerId: string): Worker {
    const w = this.workers.find((w) => w.workerId === workerId);
    if (!w) throw new Error(`Worker ${workerId} not found`);
    return w;
  }

  private emit(eventType: CoordinatorEventType, payload: Record<string, unknown>): void {
    this.events.push({
      eventId: randomUUID().slice(0, 12),
      eventType,
      timestamp: new Date().toISOString(),
      payload: { coordinatorId: this.coordinatorId, ...payload },
    });
  }
}
