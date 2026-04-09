/**
 * Canonical run-state event model and causal trace artifact.
 *
 * TS port of autocontext.analytics.run_trace (AC-381).
 */

export interface ActorRefDict {
  actor_type: string;
  actor_id: string;
  actor_name: string;
}

export interface TraceEventDict {
  event_type: string;
  actor: ActorRefDict;
  payload: Record<string, unknown>;
  timestamp: string;
}

export interface RunTraceDict {
  run_id: string;
  scenario_type: string;
  events: TraceEventDict[];
  created_at: string;
}

export class ActorRef {
  readonly actorType: string;
  readonly actorId: string;
  readonly actorName: string;

  constructor(actorType: string, actorId: string, actorName: string) {
    this.actorType = actorType;
    this.actorId = actorId;
    this.actorName = actorName;
  }

  toDict(): ActorRefDict {
    return {
      actor_type: this.actorType,
      actor_id: this.actorId,
      actor_name: this.actorName,
    };
  }

  static fromDict(data: ActorRefDict): ActorRef {
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

  toDict(): TraceEventDict {
    return {
      event_type: this.eventType,
      actor: this.actor.toDict(),
      payload: this.payload,
      timestamp: this.timestamp,
    };
  }

  static fromDict(data: TraceEventDict): TraceEvent {
    return new TraceEvent({
      eventType: data.event_type ?? "",
      actor: ActorRef.fromDict(data.actor),
      payload: data.payload ?? {},
      timestamp: data.timestamp ?? undefined,
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

  toDict(): RunTraceDict {
    return {
      run_id: this.runId,
      scenario_type: this.scenarioType,
      events: this.events.map((event) => event.toDict()),
      created_at: this.createdAt,
    };
  }

  toJSON(): string {
    return JSON.stringify(this.toDict());
  }

  static fromDict(data: RunTraceDict): RunTrace {
    const trace = new RunTrace(
      data.run_id ?? "",
      data.scenario_type ?? "",
      data.created_at ?? undefined,
    );
    const events = data.events ?? [];
    for (const event of events) {
      trace.addEvent(TraceEvent.fromDict(event));
    }
    return trace;
  }

  static fromJSON(json: string): RunTrace {
    return RunTrace.fromDict(JSON.parse(json) as RunTraceDict);
  }
}
