import { join } from "node:path";

import { ArtifactStore } from "../knowledge/artifact-store.js";
import { generateSessionReport } from "../knowledge/session-report.js";
import { ScoreTrajectoryBuilder } from "../knowledge/trajectory.js";
import type { SQLiteStore } from "../storage/index.js";
import type { GenerationAttempt } from "./generation-attempt-state.js";

export interface GenerationJournalScenario {
  name: string;
  replayToNarrative(replay: Array<Record<string, unknown>>): string;
}

export type GenerationJournalAttempt = GenerationAttempt;

export interface SessionReportContext {
  runStartedAtMs: number;
  explorationMode: string;
}

export interface GenerationJournalOpts {
  store: SQLiteStore;
  artifacts: ArtifactStore;
  scenario: GenerationJournalScenario;
}

export class GenerationJournal {
  readonly #store: SQLiteStore;
  readonly #artifacts: ArtifactStore;
  readonly #scenario: GenerationJournalScenario;

  constructor(opts: GenerationJournalOpts) {
    this.#store = opts.store;
    this.#artifacts = opts.artifacts;
    this.#scenario = opts.scenario;
  }

  persistGeneration(runId: string, generationIndex: number, attempt: GenerationJournalAttempt): void {
    this.#store.upsertGeneration(runId, generationIndex, {
      meanScore: attempt.tournamentResult.meanScore,
      bestScore: attempt.tournamentResult.bestScore,
      elo: attempt.tournamentResult.elo,
      wins: attempt.tournamentResult.wins,
      losses: attempt.tournamentResult.losses,
      gateDecision: attempt.gateDecision,
      status: "completed",
    });

    for (const match of attempt.tournamentResult.matches) {
      this.#store.recordMatch(runId, generationIndex, {
        seed: match.seed,
        score: match.score,
        passedValidation: match.passedValidation,
        validationErrors: match.validationErrors.join("; "),
        winner: match.winner ?? "",
        strategyJson: JSON.stringify(attempt.strategy),
        replayJson: JSON.stringify(match.replay),
      });
    }

    this.#store.appendAgentOutput(runId, generationIndex, "competitor", attempt.competitorResultText);

    const generationDir = this.#artifacts.generationDir(runId, generationIndex);
    this.#artifacts.writeMarkdown(join(generationDir, "competitor_prompt.md"), attempt.competitorPrompt);
    this.#artifacts.writeMarkdown(join(generationDir, "competitor_output.md"), attempt.competitorResultText);
    this.#artifacts.writeMarkdown(
      join(generationDir, "trajectory.md"),
      new ScoreTrajectoryBuilder(this.#store.getScoreTrajectory(runId)).build() || "No prior trajectory yet.",
    );

    const bestReplayMatch = attempt.tournamentResult.matches.reduce((best, current) =>
      current.score > best.score ? current : best,
    );

    this.#artifacts.writeJson(join(generationDir, "replays", `${this.#scenario.name}_${generationIndex}.json`), {
      run_id: runId,
      generation: generationIndex,
      scenario: this.#scenario.name,
      seed: bestReplayMatch.seed,
      score: bestReplayMatch.score,
      winner: bestReplayMatch.winner,
      narrative: this.#scenario.replayToNarrative(bestReplayMatch.replay),
      timeline: bestReplayMatch.replay,
      matches: attempt.tournamentResult.matches.map((match) => ({
        seed: match.seed,
        score: match.score,
        winner: match.winner,
        passed_validation: match.passedValidation,
        validation_errors: match.validationErrors,
        timeline: match.replay,
      })),
    });

    this.#artifacts.writeJson(join(generationDir, "tournament_summary.json"), {
      gate_decision: attempt.gateDecision,
      mean_score: attempt.tournamentResult.meanScore,
      best_score: attempt.tournamentResult.bestScore,
      elo: attempt.tournamentResult.elo,
      wins: attempt.tournamentResult.wins,
      losses: attempt.tournamentResult.losses,
    });
  }

  countDeadEnds(): number {
    const content = this.#artifacts.readDeadEnds(this.#scenario.name);
    if (!content) return 0;
    return content.split("\n").filter((line) => line.startsWith("### Dead End")).length;
  }

  persistSessionReport(runId: string, context: SessionReportContext): string {
    const report = generateSessionReport(
      runId,
      this.#scenario.name,
      this.#store.getScoreTrajectory(runId) as unknown as Array<Record<string, unknown>>,
      {
        durationSeconds: (Date.now() - context.runStartedAtMs) / 1000,
        deadEndsFound: this.countDeadEnds(),
        explorationMode: context.explorationMode,
      },
    );
    const markdown = report.toMarkdown();
    const runPath = join(this.#artifacts.runsRoot, runId, "session_report.md");
    this.#artifacts.writeMarkdown(runPath, markdown);
    this.#artifacts.writeSessionReport(this.#scenario.name, runId, markdown);
    return runPath;
  }
}
