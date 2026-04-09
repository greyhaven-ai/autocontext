import { ArtifactStore } from "../knowledge/artifact-store.js";
import { DeadEndEntry, consolidateDeadEnds } from "../knowledge/dead-end.js";
import { PLAYBOOK_MARKERS } from "../knowledge/playbook.js";
import type { GenerationGateDecision } from "./generation-attempt-state.js";
import type { StagnationDetector, StagnationReport } from "./stagnation.js";

export interface GenerationRecoveryOpts {
  artifacts: ArtifactStore;
  scenarioName: string;
  deadEndTrackingEnabled: boolean;
  deadEndMaxEntries: number;
  stagnationResetEnabled: boolean;
  stagnationDistillTopLessons: number;
  stagnationDetector: StagnationDetector;
}

export interface GenerationRecoveryAttempt {
  generation: number;
  gateDecision: GenerationGateDecision;
  bestScore: number;
  strategy: Record<string, unknown>;
  previousBestForGeneration: number;
}

export interface GenerationRecoveryEvent {
  event: "dead_end_recorded" | "fresh_start";
  payload: Record<string, unknown>;
}

export interface GenerationRecoveryOutcome {
  freshStartHint: string | null;
  shouldNotifyRegression: boolean;
  shouldNotifyThreshold: boolean;
  deadEndRecorded: boolean;
  events: GenerationRecoveryEvent[];
}

function extractMarkedSection(content: string, startMarker: string, endMarker: string): string {
  const start = content.indexOf(startMarker);
  const end = content.indexOf(endMarker);
  if (start === -1 || end === -1 || end <= start) return "";
  return content.slice(start + startMarker.length, end).trim();
}

export class GenerationRecovery {
  readonly #artifacts: ArtifactStore;
  readonly #scenarioName: string;
  readonly #deadEndTrackingEnabled: boolean;
  readonly #deadEndMaxEntries: number;
  readonly #stagnationResetEnabled: boolean;
  readonly #stagnationDistillTopLessons: number;
  readonly #stagnationDetector: StagnationDetector;
  #gateHistory: string[] = [];
  #scoreHistory: number[] = [];

  constructor(opts: GenerationRecoveryOpts) {
    this.#artifacts = opts.artifacts;
    this.#scenarioName = opts.scenarioName;
    this.#deadEndTrackingEnabled = opts.deadEndTrackingEnabled;
    this.#deadEndMaxEntries = opts.deadEndMaxEntries;
    this.#stagnationResetEnabled = opts.stagnationResetEnabled;
    this.#stagnationDistillTopLessons = opts.stagnationDistillTopLessons;
    this.#stagnationDetector = opts.stagnationDetector;
  }

  handleAttempt(runId: string, attempt: GenerationRecoveryAttempt): GenerationRecoveryOutcome {
    const events: GenerationRecoveryEvent[] = [];
    let deadEndRecorded = false;

    this.#gateHistory.push(attempt.gateDecision);
    this.#scoreHistory.push(attempt.bestScore);

    if (attempt.gateDecision === "rollback" && this.#deadEndTrackingEnabled) {
      const entry = DeadEndEntry.fromRollback(
        attempt.generation,
        JSON.stringify(attempt.strategy, null, 0),
        attempt.bestScore,
      );
      this.#artifacts.appendDeadEnd(this.#scenarioName, entry.toMarkdown());
      const deadEnds = this.#artifacts.readDeadEnds(this.#scenarioName);
      if (deadEnds) {
        this.#artifacts.replaceDeadEnds(
          this.#scenarioName,
          consolidateDeadEnds(deadEnds, this.#deadEndMaxEntries),
        );
      }
      deadEndRecorded = true;
      events.push({
        event: "dead_end_recorded",
        payload: {
          run_id: runId,
          generation: attempt.generation,
          score: attempt.bestScore,
        },
      });
    }

    let freshStartHint: string | null = null;
    if (this.#stagnationResetEnabled) {
      const report = this.#stagnationDetector.detect(this.#gateHistory, this.#scoreHistory);
      if (report.isStagnated) {
        freshStartHint = this.#buildFreshStartHint(report);
        events.push({
          event: "fresh_start",
          payload: {
            run_id: runId,
            generation: attempt.generation,
            trigger: report.trigger,
            detail: report.detail,
          },
        });
      }
    }

    return {
      freshStartHint,
      shouldNotifyRegression: attempt.gateDecision === "rollback",
      shouldNotifyThreshold:
        attempt.gateDecision === "advance" && attempt.bestScore > attempt.previousBestForGeneration,
      deadEndRecorded,
      events,
    };
  }

  #buildFreshStartHint(report: StagnationReport): string {
    const playbook = this.#artifacts.readPlaybook(this.#scenarioName);
    const lessons = extractMarkedSection(
      playbook,
      PLAYBOOK_MARKERS.LESSONS_START,
      PLAYBOOK_MARKERS.LESSONS_END,
    )
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.startsWith("-"))
      .slice(0, this.#stagnationDistillTopLessons);

    const deadEnds = this.#artifacts.readDeadEnds(this.#scenarioName)
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.startsWith("- **Gen"))
      .slice(-2);

    const sections = [
      `Stagnation detected via ${report.trigger}: ${report.detail}.`,
      "Treat the next generation as a fresh start rather than a small local tweak.",
    ];

    if (lessons.length > 0) {
      sections.push("Retain only these distilled lessons:");
      sections.push(...lessons);
    }

    if (deadEnds.length > 0) {
      sections.push("Avoid repeating these recent dead ends:");
      sections.push(...deadEnds);
    }

    return sections.join("\n");
  }
}
