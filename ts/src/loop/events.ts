/**
 * Event stream emitter — NDJSON file + subscriber dispatch (AC-342).
 * Mirrors Python's autocontext/harness/core/events.py.
 */

import { appendFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

export interface EventStreamRecord {
  channel: string;
  event: string;
  payload: Record<string, unknown>;
  seq: number;
  ts: string;
  v: 1;
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

  constructor(path: string) {
    this.path = path;
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

    const line: EventStreamRecord = {
      channel,
      event,
      payload,
      seq,
      ts: new Date().toISOString(),
      v: 1,
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
