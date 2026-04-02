/**
 * Research runtime plumbing (AC-498 TS parity).
 */

import { randomUUID } from "node:crypto";
import type { ResearchAdapter } from "./types.js";
import { ResearchConfig, ResearchQuery, ResearchResult } from "./types.js";

interface ResearchEvent {
  eventId: string;
  eventType: string;
  timestamp: string;
  payload: Record<string, unknown>;
}

export class ResearchEnabledSession {
  readonly sessionId: string;
  readonly goal: string;
  readonly events: ResearchEvent[] = [];
  private _adapter: ResearchAdapter | null;
  private _config: ResearchConfig;
  private _queryCount = 0;
  private _history: ResearchResult[] = [];

  private constructor(goal: string, adapter: ResearchAdapter | null, config: ResearchConfig) {
    this.sessionId = randomUUID().slice(0, 16);
    this.goal = goal;
    this._adapter = adapter;
    this._config = config;
    this.emit("session_created", { goal });
  }

  static create(opts: { goal: string; adapter?: ResearchAdapter; config?: ResearchConfig }): ResearchEnabledSession {
    return new ResearchEnabledSession(
      opts.goal,
      opts.adapter ?? null,
      opts.config ?? new ResearchConfig({ enabled: opts.adapter != null }),
    );
  }

  get hasResearch(): boolean { return this._adapter !== null; }
  get researchQueriesUsed(): number { return this._queryCount; }
  get researchHistory(): ResearchResult[] { return [...this._history]; }

  research(query: ResearchQuery): ResearchResult | null {
    if (!this._adapter) return null;
    if (this._queryCount >= this._config.maxQueriesPerSession) return null;

    const result = this._adapter.search(query);
    this._queryCount++;
    this._history.push(result);
    this.emit("research_requested", { topic: query.topic, confidence: result.confidence, citations: result.citations.length });
    return result;
  }

  private emit(eventType: string, payload: Record<string, unknown>): void {
    this.events.push({ eventId: randomUUID().slice(0, 12), eventType, timestamp: new Date().toISOString(), payload });
  }
}
