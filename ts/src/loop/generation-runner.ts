/**
 * Generation runner — core loop (AC-346 Task 21).
 * Mirrors Python's loop/generation_runner.py (simplified).
 *
 * Loop: for each generation:
 *   1. Build prompts from scenario + knowledge
 *   2. Orchestrate agents (competitor → analyst/coach/architect)
 *   3. Extract strategy → run tournament
 *   4. Backpressure gate (advance/retry/rollback)
 *   5. Persist to SQLite + artifacts
 */

import type { LLMProvider } from "../types/index.js";
import type { ScenarioInterface } from "../scenarios/game-interface.js";
import type { SQLiteStore } from "../storage/index.js";
import { TournamentRunner } from "../execution/tournament.js";
import { BackpressureGate } from "./backpressure.js";
import { ArtifactStore } from "../knowledge/artifact-store.js";
import { PlaybookGuard, PLAYBOOK_MARKERS } from "../knowledge/playbook.js";
import { ScoreTrajectoryBuilder } from "../knowledge/trajectory.js";
import { ContextBudget } from "../prompts/context-budget.js";
import { join } from "node:path";

export interface GenerationRunnerOpts {
  provider: LLMProvider;
  scenario: ScenarioInterface;
  store: SQLiteStore;
  runsRoot: string;
  knowledgeRoot: string;
  matchesPerGeneration?: number;
  maxRetries?: number;
  minDelta?: number;
  seedBase?: number;
}

export interface RunResult {
  runId: string;
  generationsCompleted: number;
  bestScore: number;
  currentElo: number;
}

export class GenerationRunner {
  private provider: LLMProvider;
  private scenario: ScenarioInterface;
  private store: SQLiteStore;
  private artifactStore: ArtifactStore;
  private matchesPerGeneration: number;
  private maxRetries: number;
  private gate: BackpressureGate;
  private seedBase: number;
  private playbookGuard: PlaybookGuard;
  private contextBudget: ContextBudget;

  constructor(opts: GenerationRunnerOpts) {
    this.provider = opts.provider;
    this.scenario = opts.scenario;
    this.store = opts.store;
    this.artifactStore = new ArtifactStore({
      runsRoot: opts.runsRoot,
      knowledgeRoot: opts.knowledgeRoot,
    });
    this.matchesPerGeneration = opts.matchesPerGeneration ?? 3;
    this.maxRetries = opts.maxRetries ?? 2;
    this.gate = new BackpressureGate(opts.minDelta ?? 0.005);
    this.seedBase = opts.seedBase ?? 1000;
    this.playbookGuard = new PlaybookGuard();
    this.contextBudget = new ContextBudget();
  }

  async run(runId: string, generations: number): Promise<RunResult> {
    // Create run record
    this.store.createRun(runId, this.scenario.name, generations, "local");

    let previousBest = 0;
    let currentElo = 1000;
    let bestScoreOverall = 0;

    for (let gen = 1; gen <= generations; gen++) {
      let retryCount = 0;
      let finalizedAttempt: GenerationAttempt | null = null;

      // Retry loop for this generation
      while (retryCount <= this.maxRetries) {
        const competitorPrompt = this.buildCompetitorPrompt(runId);

        // Step 1: Get strategy from provider (competitor role)
        const competitorResult = await this.provider.complete({
          systemPrompt: "",
          userPrompt: competitorPrompt,
        });

        let strategy: Record<string, unknown>;
        try {
          strategy = JSON.parse(competitorResult.text);
        } catch {
          strategy = { aggression: 0.5, defense: 0.5, path_bias: 0.5 };
        }

        // Step 2: Run tournament
        const seedForGen = this.seedBase + (gen - 1) * this.matchesPerGeneration;
        const tournament = new TournamentRunner(this.scenario, {
          matchCount: this.matchesPerGeneration,
          seedBase: seedForGen,
          initialElo: currentElo,
        });
        const tournamentResult = tournament.run(strategy);

        // Step 3: Backpressure gate
        const decision = this.gate.evaluate(
          previousBest,
          tournamentResult.bestScore,
          retryCount,
          this.maxRetries,
        );
        const gateDecision = decision.decision;
        const attempt: GenerationAttempt = {
          competitorPrompt,
          competitorResultText: competitorResult.text,
          strategy,
          tournamentResult,
          gateDecision,
        };

        // Step 5: Apply gate decision
        if (gateDecision === "advance") {
          finalizedAttempt = attempt;
          previousBest = tournamentResult.bestScore;
          currentElo = tournamentResult.elo;
          if (tournamentResult.bestScore > bestScoreOverall) {
            bestScoreOverall = tournamentResult.bestScore;
          }
          break;
        }

        if (gateDecision === "retry") {
          retryCount++;
          continue;
        }

        // rollback — don't update previousBest, move to next gen
        finalizedAttempt = attempt;
        break;
      }

      if (!finalizedAttempt) {
        throw new Error(`generation ${gen} finished without a finalized attempt`);
      }

      this.persistGeneration(runId, gen, finalizedAttempt);
      await this.runSupportRoles(runId, gen, finalizedAttempt);
    }

    return {
      runId,
      generationsCompleted: generations,
      bestScore: bestScoreOverall,
      currentElo,
    };
  }

  private buildCompetitorPrompt(runId: string): string {
    const trimmed = this.contextBudget.apply({
      playbook: this.artifactStore.readPlaybook(this.scenario.name),
      trajectory: new ScoreTrajectoryBuilder(this.store.getScoreTrajectory(runId)).build(),
    });

    const sections = [
      "Describe your strategy for the " + this.scenario.name + " scenario. Return JSON with the strategy parameters.",
      `Scenario Rules:\n${this.scenario.describeRules()}`,
      `Strategy Interface:\n${this.scenario.describeStrategyInterface()}`,
      `Evaluation Criteria:\n${this.scenario.describeEvaluationCriteria()}`,
      `Current Playbook:\n${trimmed.playbook}`,
    ];

    if (trimmed.trajectory) {
      sections.push(`Recent Score Trajectory:\n${trimmed.trajectory}`);
    }

    sections.push(
      "Respond with JSON only. Include the strategy fields required by the strategy interface.",
    );

    return sections.join("\n\n");
  }

  private buildSupportPrompt(
    role: "analyst" | "coach",
    runId: string,
    attempt: GenerationAttempt,
  ): string {
    const trimmed = this.contextBudget.apply({
      playbook: this.artifactStore.readPlaybook(this.scenario.name),
      trajectory: new ScoreTrajectoryBuilder(this.store.getScoreTrajectory(runId)).build(),
      analysis:
        `Gate decision: ${attempt.gateDecision}\n` +
        `Best score: ${attempt.tournamentResult.bestScore.toFixed(4)}\n` +
        `Mean score: ${attempt.tournamentResult.meanScore.toFixed(4)}\n` +
        `Wins/Losses: ${attempt.tournamentResult.wins}/${attempt.tournamentResult.losses}`,
    });

    const intro =
      role === "analyst"
        ? `Analyze strengths/failures of the current strategy for ${this.scenario.name}.`
        : `You are the playbook coach. Update the playbook for ${this.scenario.name}.`;

    const sections = [
      intro,
      `Scenario Rules:\n${this.scenario.describeRules()}`,
      `Strategy Interface:\n${this.scenario.describeStrategyInterface()}`,
      `Current Strategy JSON:\n${JSON.stringify(attempt.strategy, null, 2)}`,
      `Tournament Summary:\n${trimmed.analysis}`,
      `Current Playbook:\n${trimmed.playbook}`,
    ];

    if (trimmed.trajectory) {
      sections.push(`Recent Score Trajectory:\n${trimmed.trajectory}`);
    }

    return sections.join("\n\n");
  }

  private persistGeneration(runId: string, gen: number, attempt: GenerationAttempt): void {
    this.store.upsertGeneration(runId, gen, {
      meanScore: attempt.tournamentResult.meanScore,
      bestScore: attempt.tournamentResult.bestScore,
      elo: attempt.tournamentResult.elo,
      wins: attempt.tournamentResult.wins,
      losses: attempt.tournamentResult.losses,
      gateDecision: attempt.gateDecision,
      status: "completed",
    });

    for (const match of attempt.tournamentResult.matches) {
      this.store.recordMatch(runId, gen, {
        seed: match.seed,
        score: match.score,
        passedValidation: match.passedValidation,
        validationErrors: match.validationErrors.join("; "),
        winner: match.winner ?? "",
        strategyJson: JSON.stringify(attempt.strategy),
        replayJson: JSON.stringify(match.replay),
      });
    }

    this.store.appendAgentOutput(runId, gen, "competitor", attempt.competitorResultText);

    const generationDir = this.artifactStore.generationDir(runId, gen);
    this.artifactStore.writeMarkdown(
      join(generationDir, "competitor_prompt.md"),
      attempt.competitorPrompt,
    );
    this.artifactStore.writeMarkdown(
      join(generationDir, "competitor_output.md"),
      attempt.competitorResultText,
    );
    this.artifactStore.writeMarkdown(
      join(generationDir, "trajectory.md"),
      new ScoreTrajectoryBuilder(this.store.getScoreTrajectory(runId)).build() || "No prior trajectory yet.",
    );
    this.artifactStore.writeJson(join(generationDir, "tournament_summary.json"), {
      gate_decision: attempt.gateDecision,
      mean_score: attempt.tournamentResult.meanScore,
      best_score: attempt.tournamentResult.bestScore,
      elo: attempt.tournamentResult.elo,
      wins: attempt.tournamentResult.wins,
      losses: attempt.tournamentResult.losses,
    });
  }

  private async runSupportRoles(
    runId: string,
    gen: number,
    attempt: GenerationAttempt,
  ): Promise<void> {
    const [analystResult, coachResult] = await Promise.all([
      this.provider.complete({
        systemPrompt: "",
        userPrompt: this.buildSupportPrompt("analyst", runId, attempt),
      }),
      this.provider.complete({
        systemPrompt: "",
        userPrompt: this.buildSupportPrompt("coach", runId, attempt),
      }),
    ]);

    this.store.appendAgentOutput(runId, gen, "analyst", analystResult.text);
    this.store.appendAgentOutput(runId, gen, "coach", coachResult.text);

    const generationDir = this.artifactStore.generationDir(runId, gen);
    this.artifactStore.writeMarkdown(join(generationDir, "analyst.md"), analystResult.text);
    this.artifactStore.writeMarkdown(join(generationDir, "coach.md"), coachResult.text);
    this.artifactStore.appendMarkdown(
      join(this.artifactStore.runsRoot, runId, "support_log.md"),
      analystResult.text,
      `Generation ${gen} Analyst`,
    );
    this.artifactStore.appendMarkdown(
      join(this.artifactStore.runsRoot, runId, "support_log.md"),
      coachResult.text,
      `Generation ${gen} Coach`,
    );

    const currentPlaybook = this.artifactStore.readPlaybook(this.scenario.name);
    const hasStructuredPlaybook =
      coachResult.text.includes(PLAYBOOK_MARKERS.PLAYBOOK_START) &&
      coachResult.text.includes(PLAYBOOK_MARKERS.PLAYBOOK_END) &&
      coachResult.text.includes(PLAYBOOK_MARKERS.LESSONS_START) &&
      coachResult.text.includes(PLAYBOOK_MARKERS.LESSONS_END) &&
      coachResult.text.includes(PLAYBOOK_MARKERS.HINTS_START) &&
      coachResult.text.includes(PLAYBOOK_MARKERS.HINTS_END);
    const playbookCheck = this.playbookGuard.check(currentPlaybook, coachResult.text);
    if (hasStructuredPlaybook && playbookCheck.approved) {
      this.artifactStore.writePlaybook(this.scenario.name, coachResult.text);
    }
  }
}

interface GenerationAttempt {
  competitorPrompt: string;
  competitorResultText: string;
  strategy: Record<string, unknown>;
  tournamentResult: ReturnType<TournamentRunner["run"]>;
  gateDecision: "advance" | "retry" | "rollback";
}
