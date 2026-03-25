/**
 * Canonical run-state event model and causal trace artifact.
 *
 * TS port of autocontext.analytics.run_trace (AC-381).
 */

export class ActorRef {
  readonly actorType: string;
  readonly actorId: string;
  readonly actorName: string;

  constructor(actorType: string, actorId: string, actorName: string) {
    this.actorType = actorType;
    this.actorId = actorId;
    this.actorName = actorName;
  }

  toDict(): Record<string, string> {
    return {
      actor_type: this.actorType,
      actor_id: this.actorId,
      actor_name: this.actorName,
    };
  }

  static fromDict(data: Record<string, string>): ActorRef {
    return new ActorRef(
      data.actor_type ?? "",
      data.actor_id ?? "",
      data.actor_name ?? "",
    );
  }
}

export interface TraceEventInit {
  eventType: string;
  actor: ActorRef;
  payload?: Record<string, unknown>;
  timestamp?: string;
}

export class TraceEvent {
  readonly eventType: string;
  readonly actor: ActorRef;
  readonly payload: Record<string, unknown>;
  readonly timestamp: string;

  constructor(init: TraceEventInit) {
    this.eventType = init.eventType;
    this.actor = init.actor;
    this.payload = init.payload ?? {};
    this.timestamp = init.timestamp ?? new Date().toISOString();
  }

  toDict(): Record<string, unknown> {
    return {
      event_type: this.eventType,
      actor: this.actor.toDict(),
      payload: this.payload,
      timestamp: this.timestamp,
    };
  }

  static fromDict(data: Record<string, unknown>): TraceEvent {
    return new TraceEvent({
      eventType: (data.event_type as string) ?? "",
      actor: ActorRef.fromDict(data.actor as Record<string, string>),
      payload: (data.payload as Record<string, unknown>) ?? {},
      timestamp: (data.timestamp as string) ?? undefined,
    });
  }
}

export class RunTrace {
  readonly runId: string;
  readonly scenarioType: string;
  readonly events: TraceEvent[] = [];
  readonly createdAt: string;

  constructor(runId: string, scenarioType: string, createdAt?: string) {
    this.runId = runId;
    this.scenarioType = scenarioType;
    this.createdAt = createdAt ?? new Date().toISOString();
  }

  addEvent(event: TraceEvent): void {
    this.events.push(event);
  }

  toDict(): Record<string, unknown> {
    return {
      run_id: this.runId,
      scenario_type: this.scenarioType,
      events: this.events.map((e) => e.toDict()),
      created_at: this.createdAt,
    };
  }

  toJSON(): string {
    return JSON.stringify(this.toDict());
  }

  static fromDict(data: Record<string, unknown>): RunTrace {
    const trace = new RunTrace(
      (data.run_id as string) ?? "",
      (data.scenario_type as string) ?? "",
      (data.created_at as string) ?? undefined,
    );
    const events = (data.events as Record<string, unknown>[]) ?? [];
    for (const event of events) {
      trace.addEvent(TraceEvent.fromDict(event));
    }
    return trace;
  }

  static fromJSON(json: string): RunTrace {
    return RunTrace.fromDict(JSON.parse(json) as Record<string, unknown>);
  }
}
