/**
 * Event stream emitter — NDJSON file + subscriber dispatch (AC-342).
 * Mirrors Python's autocontext/harness/core/events.py.
 */

import { appendFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

import { EventTraceTracker, type EventTraceSpan } from "./event-trace.js";

export interface EventStreamRecord {
  channel: string;
  event: string;
  payload: Record<string, unknown>;
  seq: number;
  ts: string;
  v: 1;
  trace?: EventTraceSpan;
}

export type EventCallback = (
  event: string,
  payload: Record<string, unknown>,
  record?: EventStreamRecord,
) => void;

export class EventStreamEmitter {
  readonly path: string;
  #sequence = 0;
  #subscribers: EventCallback[] = [];
  readonly #traceTracker = new EventTraceTracker();
  readonly #now: () => Date;

  constructor(path: string, opts: { now?: () => Date } = {}) {
    this.path = path;
    this.#now = opts.now ?? (() => new Date());
  }

  subscribe(callback: EventCallback): void {
    this.#subscribers.push(callback);
  }

  unsubscribe(callback: EventCallback): void {
    const idx = this.#subscribers.indexOf(callback);
    if (idx !== -1) {
      this.#subscribers.splice(idx, 1);
    }
  }

  emit(
    event: string,
    payload: Record<string, unknown>,
    channel = "generation",
  ): void {
    // Ensure parent directory exists
    mkdirSync(dirname(this.path), { recursive: true });

    this.#sequence += 1;
    const seq = this.#sequence;
    const subscribersCopy = [...this.#subscribers];

    const ts = this.#now().toISOString();
    const line: EventStreamRecord = {
      channel,
      event,
      payload,
      seq,
      ts,
      v: 1,
      trace: this.#traceTracker.trace(event, payload, ts, seq),
    };

    appendFileSync(this.path, JSON.stringify(line) + "\n", "utf-8");

    for (const cb of subscribersCopy) {
      try {
        cb(event, payload, line);
      } catch {
        // subscriber errors must never crash the loop
      }
    }
  }
}
