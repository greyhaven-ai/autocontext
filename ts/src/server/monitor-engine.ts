import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";

import type { EventStreamEmitter } from "../loop/events.js";
import type {
  InsertMonitorAlertOpts,
  InsertMonitorConditionOpts,
  MonitorAlertRow,
  MonitorConditionRow,
  SQLiteStore,
} from "../storage/index.js";

type MonitorConditionType =
  | "metric_threshold"
  | "stall_window"
  | "artifact_created"
  | "process_exit"
  | "heartbeat_lost";

type AlertWaiter = (alert: MonitorAlertRow | null) => void;

export interface MonitorEngineOpts {
  store: SQLiteStore;
  emitter: EventStreamEmitter;
  defaultHeartbeatTimeoutSeconds: number;
  maxConditions: number;
  heartbeatIntervalMs?: number;
}

export class MonitorEngine {
  readonly #store: SQLiteStore;
  readonly #emitter: EventStreamEmitter;
  readonly #defaultHeartbeatTimeoutSeconds: number;
  readonly #maxConditions: number;
  readonly #heartbeatIntervalMs: number;
  readonly #waiters = new Map<string, Set<AlertWaiter>>();
  readonly #onEvent = (event: string, payload: Record<string, unknown>) => {
    this.#handleEvent(event, payload);
  };
  #lastEventMs = Date.now();
  #heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  #heartbeatFiredConditionIds = new Set<string>();
  #running = false;

  constructor(opts: MonitorEngineOpts) {
    this.#store = opts.store;
    this.#emitter = opts.emitter;
    this.#defaultHeartbeatTimeoutSeconds = opts.defaultHeartbeatTimeoutSeconds;
    this.#maxConditions = opts.maxConditions;
    this.#heartbeatIntervalMs = opts.heartbeatIntervalMs ?? 1000;
  }

  start(): void {
    if (this.#running) return;
    this.#running = true;
    this.#lastEventMs = Date.now();
    this.#heartbeatFiredConditionIds.clear();
    this.#emitter.subscribe(this.#onEvent);
    this.#heartbeatTimer = setInterval(() => {
      this.#checkHeartbeat();
    }, this.#heartbeatIntervalMs);
    this.#heartbeatTimer.unref?.();
  }

  stop(): void {
    if (!this.#running) return;
    this.#running = false;
    this.#emitter.unsubscribe(this.#onEvent);
    if (this.#heartbeatTimer) {
      clearInterval(this.#heartbeatTimer);
      this.#heartbeatTimer = null;
    }
    this.#heartbeatFiredConditionIds.clear();
    for (const waiters of this.#waiters.values()) {
      for (const waiter of waiters) {
        waiter(null);
      }
    }
    this.#waiters.clear();
  }

  createCondition(opts: InsertMonitorConditionOpts): string {
    if (this.#store.countMonitorConditions({ activeOnly: true }) >= this.#maxConditions) {
      throw new Error(`maximum active monitor conditions reached (${this.#maxConditions})`);
    }
    const params = opts.conditionType === "heartbeat_lost"
      && opts.params?.timeout_seconds === undefined
      ? { ...(opts.params ?? {}), timeout_seconds: this.#defaultHeartbeatTimeoutSeconds }
      : opts.params;
    return this.#store.insertMonitorCondition({
      ...opts,
      params,
    });
  }

  async waitForAlert(
    conditionId: string,
    timeoutSeconds: number,
  ): Promise<MonitorAlertRow | null> {
    const existing = this.#store.getLatestMonitorAlert(conditionId);
    if (existing) return existing;

    return new Promise((resolve) => {
      let settled = false;
      const waiters = this.#waiters.get(conditionId) ?? new Set<AlertWaiter>();
      const finish = (alert: MonitorAlertRow | null) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        waiters.delete(finish);
        if (waiters.size === 0) {
          this.#waiters.delete(conditionId);
        }
        resolve(alert);
      };
      const timer = setTimeout(() => finish(null), Math.max(0, timeoutSeconds) * 1000);
      timer.unref?.();
      waiters.add(finish);
      this.#waiters.set(conditionId, waiters);
    });
  }

  #handleEvent(event: string, payload: Record<string, unknown>): void {
    this.#lastEventMs = Date.now();
    this.#heartbeatFiredConditionIds.clear();
    for (const condition of this.#store.listMonitorConditions({ activeOnly: true })) {
      const alert = this.#evaluateCondition(event, payload, condition);
      if (alert) {
        this.#fireAlert(alert);
      }
    }
  }

  #checkHeartbeat(): void {
    const now = Date.now();
    for (const condition of this.#store.listMonitorConditions({ activeOnly: true })) {
      if (condition.condition_type !== "heartbeat_lost") continue;
      if (this.#heartbeatFiredConditionIds.has(condition.id)) continue;
      const alert = this.#evaluateHeartbeat(condition, now);
      if (alert) {
        this.#heartbeatFiredConditionIds.add(condition.id);
        this.#fireAlert(alert);
      }
    }
  }

  #evaluateCondition(
    event: string,
    payload: Record<string, unknown>,
    condition: MonitorConditionRow,
  ): InsertMonitorAlertOpts | null {
    if (!isConditionType(condition.condition_type)) return null;
    if (condition.condition_type === "metric_threshold") {
      return evaluateMetricThreshold(event, payload, condition);
    }
    if (condition.condition_type === "stall_window") {
      return evaluateStallWindow(event, payload, condition);
    }
    if (condition.condition_type === "artifact_created") {
      return evaluateArtifactCreated(event, payload, condition);
    }
    if (condition.condition_type === "process_exit") {
      return evaluateProcessExit(event, payload, condition);
    }
    return null;
  }

  #evaluateHeartbeat(
    condition: MonitorConditionRow,
    nowMs: number,
  ): InsertMonitorAlertOpts | null {
    const timeout = readNumber(condition.params.timeout_seconds, this.#defaultHeartbeatTimeoutSeconds);
    const elapsed = (nowMs - this.#lastEventMs) / 1000;
    if (elapsed <= timeout) return null;
    return buildAlert(condition, {
      detail: `No events for ${elapsed.toFixed(1)}s (timeout=${timeout.toFixed(1)}s)`,
      payload: { elapsed, timeout },
    });
  }

  #fireAlert(alert: InsertMonitorAlertOpts): void {
    this.#store.insertMonitorAlert(alert);
    const row = this.#store.getLatestMonitorAlert(alert.conditionId);
    if (!row) return;

    this.#emitter.emit("monitor_alert", {
      alert_id: row.id,
      condition_id: row.condition_id,
      condition_name: row.condition_name,
      condition_type: row.condition_type,
      scope: row.scope,
      detail: row.detail,
    }, "monitor");

    const waiters = this.#waiters.get(row.condition_id);
    if (waiters) {
      for (const waiter of [...waiters]) {
        waiter(row);
      }
    }
  }
}

function evaluateMetricThreshold(
  _event: string,
  payload: Record<string, unknown>,
  condition: MonitorConditionRow,
): InsertMonitorAlertOpts | null {
  if (!scopeMatches(payload, condition.scope)) return null;
  const metric = typeof condition.params.metric === "string" ? condition.params.metric : "";
  const threshold = readNumber(condition.params.threshold, Number.NaN);
  const direction = condition.params.direction === "below" ? "below" : "above";
  const value = readNumber(payload[metric], Number.NaN);
  if (!metric || !Number.isFinite(threshold) || !Number.isFinite(value)) return null;
  const fired = direction === "above" ? value >= threshold : value <= threshold;
  if (!fired) return null;
  return buildAlert(condition, {
    detail: `${metric}=${value} ${direction} threshold ${threshold}`,
    payload: { metric, value, threshold, direction },
  });
}

function evaluateStallWindow(
  _event: string,
  payload: Record<string, unknown>,
  condition: MonitorConditionRow,
): InsertMonitorAlertOpts | null {
  if (!scopeMatches(payload, condition.scope)) return null;
  const gateHistory = Array.isArray(payload.gate_history)
    ? payload.gate_history.filter((value): value is string => typeof value === "string")
    : [];
  const window = Math.max(1, Math.trunc(readNumber(condition.params.window, 3)));
  if (gateHistory.length < window) return null;
  let consecutive = 0;
  for (const decision of [...gateHistory].reverse()) {
    if (decision === "advance") break;
    consecutive += 1;
  }
  if (consecutive < window) return null;
  return buildAlert(condition, {
    detail: `${consecutive} consecutive non-advance decisions (window=${window})`,
    payload: { consecutive, window, tail: gateHistory.slice(-window) },
  });
}

function evaluateArtifactCreated(
  _event: string,
  payload: Record<string, unknown>,
  condition: MonitorConditionRow,
): InsertMonitorAlertOpts | null {
  if (!scopeMatches(payload, condition.scope)) return null;
  const path = typeof condition.params.path === "string" ? condition.params.path : "";
  if (!path || !existsSync(path)) return null;
  return buildAlert(condition, {
    detail: `Artifact found at ${path}`,
    payload: { path },
  });
}

function evaluateProcessExit(
  event: string,
  payload: Record<string, unknown>,
  condition: MonitorConditionRow,
): InsertMonitorAlertOpts | null {
  if (event !== "run_completed" && event !== "process_exit") return null;
  if (!scopeMatches(payload, condition.scope)) return null;
  return buildAlert(condition, {
    detail: `Process exit: event=${event}`,
    payload,
  });
}

function buildAlert(
  condition: MonitorConditionRow,
  opts: {
    detail: string;
    payload: Record<string, unknown>;
  },
): InsertMonitorAlertOpts {
  return {
    id: randomUUID().replace(/-/g, ""),
    conditionId: condition.id,
    conditionName: condition.name,
    conditionType: condition.condition_type,
    scope: condition.scope,
    detail: opts.detail,
    payload: opts.payload,
    firedAt: new Date().toISOString(),
  };
}

function scopeMatches(payload: Record<string, unknown>, scope: string): boolean {
  if (scope === "global") return true;
  if (scope.startsWith("run:")) {
    return String(payload.run_id ?? "") === scope.slice(4);
  }
  return false;
}

function readNumber(value: unknown, fallback: number): number {
  if (typeof value === "number") return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }
  return fallback;
}

function isConditionType(value: string): value is MonitorConditionType {
  return value === "metric_threshold"
    || value === "stall_window"
    || value === "artifact_created"
    || value === "process_exit"
    || value === "heartbeat_lost";
}
