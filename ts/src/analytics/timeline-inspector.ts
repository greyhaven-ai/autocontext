/**
 * Timeline and state inspector for runs and generations.
 *
 * TS port of autocontext.analytics.timeline_inspector (AC-381).
 */

export interface TimelineEvent {
  type: string;
  generation: number;
  timestamp: string;
  [key: string]: unknown;
}

export interface GenerationSummary {
  generation: number;
  events: TimelineEvent[];
  gateDecision: string | null;
  meanScore: number | null;
}

export interface TimelineSummary {
  generations: GenerationSummary[];
  totalEvents: number;
}

export class TimelineInspector {
  #events: TimelineEvent[] = [];

  addEvent(event: TimelineEvent): void {
    this.#events.push(event);
  }

  summarize(): TimelineSummary {
    const genMap = new Map<number, TimelineEvent[]>();

    for (const event of this.#events) {
      const gen = event.generation;
      const existing = genMap.get(gen) ?? [];
      existing.push(event);
      genMap.set(gen, existing);
    }

    const generations: GenerationSummary[] = [];
    for (const [gen, events] of [...genMap.entries()].sort((a, b) => a[0] - b[0])) {
      const gateEvent = events.find((e) => e.type === "gate_decided");
      const scoreEvent = events.find((e) => e.type === "tournament_completed");

      generations.push({
        generation: gen,
        events,
        gateDecision: gateEvent ? (gateEvent.decision as string) : null,
        meanScore: scoreEvent ? (scoreEvent.mean_score as number) : null,
      });
    }

    return {
      generations,
      totalEvents: this.#events.length,
    };
  }
}
