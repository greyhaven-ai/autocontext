/**
 * Tests for AC-346: Generation Loop — Deterministic Provider, Backpressure,
 * Generation Runner, CLI run.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-genloop-"));
}

// ---------------------------------------------------------------------------
// Task 19: Deterministic Provider
// ---------------------------------------------------------------------------

describe("DeterministicProvider", () => {
  it("should be importable", async () => {
    const { DeterministicProvider } = await import("../src/providers/deterministic.js");
    expect(DeterministicProvider).toBeDefined();
  });

  it("implements LLMProvider interface", async () => {
    const { DeterministicProvider } = await import("../src/providers/deterministic.js");
    const provider = new DeterministicProvider();
    expect(provider.name).toBe("deterministic");
    expect(typeof provider.defaultModel).toBe("function");
    expect(typeof provider.complete).toBe("function");
  });

  it("returns canned competitor response for strategy prompts", async () => {
    const { DeterministicProvider } = await import("../src/providers/deterministic.js");
    const provider = new DeterministicProvider();
    const result = await provider.complete({
      systemPrompt: "",
      userPrompt: "Describe your strategy for the grid scenario",
    });
    expect(result.text.length).toBeGreaterThan(0);
    // Should contain JSON-like strategy content
    expect(result.text).toContain("aggression");
  });

  it("returns canned analyst response", async () => {
    const { DeterministicProvider } = await import("../src/providers/deterministic.js");
    const provider = new DeterministicProvider();
    const result = await provider.complete({
      systemPrompt: "",
      userPrompt: "Analyze strengths/failures of the current strategy",
    });
    expect(result.text).toContain("Findings");
  });

  it("returns canned coach response with markers", async () => {
    const { DeterministicProvider } = await import("../src/providers/deterministic.js");
    const provider = new DeterministicProvider();
    const result = await provider.complete({
      systemPrompt: "",
      userPrompt: "You are the playbook coach. Update the playbook.",
    });
    expect(result.text).toContain("<!-- PLAYBOOK_START -->");
    expect(result.text).toContain("<!-- PLAYBOOK_END -->");
    expect(result.text).toContain("<!-- LESSONS_START -->");
    expect(result.text).toContain("<!-- COMPETITOR_HINTS_START -->");
  });

  it("returns default architect response with tools", async () => {
    const { DeterministicProvider } = await import("../src/providers/deterministic.js");
    const provider = new DeterministicProvider();
    const result = await provider.complete({
      systemPrompt: "",
      userPrompt: "Propose tool improvements for the harness",
    });
    expect(result.text).toContain("tools");
  });

  it("is registered in createProvider factory", async () => {
    const { createProvider } = await import("../src/providers/index.js");
    const provider = createProvider({ providerType: "deterministic" });
    expect(provider.name).toBe("deterministic");
  });
});

// ---------------------------------------------------------------------------
// Task 20: Backpressure Gate
// ---------------------------------------------------------------------------

describe("BackpressureGate", () => {
  it("should be importable", async () => {
    const { BackpressureGate } = await import("../src/loop/backpressure.js");
    expect(BackpressureGate).toBeDefined();
  });

  it("advance when delta >= threshold", async () => {
    const { BackpressureGate } = await import("../src/loop/backpressure.js");
    const gate = new BackpressureGate(0.005);
    const decision = gate.evaluate(0.50, 0.60, 0, 2);
    expect(decision.decision).toBe("advance");
    expect(decision.delta).toBeCloseTo(0.10);
  });

  it("retry when delta < threshold and retries remain", async () => {
    const { BackpressureGate } = await import("../src/loop/backpressure.js");
    const gate = new BackpressureGate(0.005);
    const decision = gate.evaluate(0.50, 0.501, 0, 2);
    expect(decision.decision).toBe("retry");
  });

  it("rollback when delta < threshold and retries exhausted", async () => {
    const { BackpressureGate } = await import("../src/loop/backpressure.js");
    const gate = new BackpressureGate(0.005);
    const decision = gate.evaluate(0.50, 0.501, 2, 2);
    expect(decision.decision).toBe("rollback");
  });

  it("advance on exact threshold", async () => {
    const { BackpressureGate } = await import("../src/loop/backpressure.js");
    const gate = new BackpressureGate(0.005);
    const decision = gate.evaluate(0.50, 0.505, 0, 2);
    expect(decision.decision).toBe("advance");
  });

  it("rollback on negative delta (regression)", async () => {
    const { BackpressureGate } = await import("../src/loop/backpressure.js");
    const gate = new BackpressureGate(0.005);
    const decision = gate.evaluate(0.60, 0.50, 2, 2);
    expect(decision.decision).toBe("rollback");
    expect(decision.delta).toBeLessThan(0);
  });
});

describe("TrendAwareGate", () => {
  it("should be importable", async () => {
    const { TrendAwareGate } = await import("../src/loop/backpressure.js");
    expect(TrendAwareGate).toBeDefined();
  });

  it("behaves like simple gate without history", async () => {
    const { TrendAwareGate } = await import("../src/loop/backpressure.js");
    const gate = new TrendAwareGate({ minDelta: 0.005 });
    const decision = gate.evaluate(0.50, 0.60, 0, 2);
    expect(decision.decision).toBe("advance");
  });

  it("relaxes threshold on plateau", async () => {
    const { TrendAwareGate } = await import("../src/loop/backpressure.js");
    const gate = new TrendAwareGate({
      minDelta: 0.01,
      plateauWindow: 3,
      plateauRelaxationFactor: 0.5,
    });
    // Plateau: scores haven't moved
    const history = {
      scores: [0.50, 0.501, 0.502, 0.501],
      gateDecisions: ["retry", "retry", "retry"],
    };
    // Delta of 0.006 is < 0.01 threshold but >= 0.005 relaxed threshold
    const decision = gate.evaluate(0.50, 0.506, 0, 2, history);
    expect(decision.decision).toBe("advance");
  });

  it("relaxes on consecutive rollbacks", async () => {
    const { TrendAwareGate } = await import("../src/loop/backpressure.js");
    const gate = new TrendAwareGate({
      minDelta: 0.01,
      consecutiveRollbackThreshold: 3,
      plateauRelaxationFactor: 0.5,
    });
    const history = {
      scores: [0.50, 0.49, 0.48, 0.47],
      gateDecisions: ["rollback", "rollback", "rollback"],
    };
    const decision = gate.evaluate(0.47, 0.476, 0, 2, history);
    expect(decision.decision).toBe("advance");
  });
});

// ---------------------------------------------------------------------------
// Task 21: Generation Runner
// ---------------------------------------------------------------------------

describe("GenerationRunner", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("should be importable", async () => {
    const { GenerationRunner } = await import("../src/loop/generation-runner.js");
    expect(GenerationRunner).toBeDefined();
  });

  it("runs a single generation with deterministic provider", async () => {
    const { GenerationRunner } = await import("../src/loop/generation-runner.js");
    const { DeterministicProvider } = await import("../src/providers/deterministic.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const { SQLiteStore } = await import("../src/storage/index.js");

    const dbPath = join(dir, "test.db");
    const store = new SQLiteStore(dbPath);
    store.migrate(join(__dirname, "..", "migrations"));

    const runner = new GenerationRunner({
      provider: new DeterministicProvider(),
      scenario: new GridCtfScenario(),
      store,
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
      matchesPerGeneration: 2,
      maxRetries: 1,
      minDelta: 0.005,
    });

    const result = await runner.run("test-run", 1);
    expect(result.runId).toBe("test-run");
    expect(result.generationsCompleted).toBe(1);
    expect(typeof result.bestScore).toBe("number");
    expect(result.bestScore).toBeGreaterThanOrEqual(0);

    store.close();
  });

  it("runs multiple generations", async () => {
    const { GenerationRunner } = await import("../src/loop/generation-runner.js");
    const { DeterministicProvider } = await import("../src/providers/deterministic.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const { SQLiteStore } = await import("../src/storage/index.js");

    const dbPath = join(dir, "test.db");
    const store = new SQLiteStore(dbPath);
    store.migrate(join(__dirname, "..", "migrations"));

    const runner = new GenerationRunner({
      provider: new DeterministicProvider(),
      scenario: new GridCtfScenario(),
      store,
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
      matchesPerGeneration: 2,
      maxRetries: 0,
      minDelta: 0.0,
    });

    const result = await runner.run("test-run-multi", 3);
    expect(result.generationsCompleted).toBe(3);
    expect(result.bestScore).toBeGreaterThanOrEqual(0);

    // Verify storage was populated
    const gens = store.getGenerations("test-run-multi");
    expect(gens.length).toBe(3);

    store.close();
  });

  it("persists matches to storage", async () => {
    const { GenerationRunner } = await import("../src/loop/generation-runner.js");
    const { DeterministicProvider } = await import("../src/providers/deterministic.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const { SQLiteStore } = await import("../src/storage/index.js");

    const dbPath = join(dir, "test.db");
    const store = new SQLiteStore(dbPath);
    store.migrate(join(__dirname, "..", "migrations"));

    const runner = new GenerationRunner({
      provider: new DeterministicProvider(),
      scenario: new GridCtfScenario(),
      store,
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
      matchesPerGeneration: 3,
      maxRetries: 0,
      minDelta: 0.0,
    });

    await runner.run("test-matches", 1);
    const matches = store.getMatchesForRun("test-matches");
    expect(matches.length).toBe(3);

    store.close();
  });

  it("uses playbook and trajectory context in live prompts and persists artifacts", async () => {
    const { GenerationRunner } = await import("../src/loop/generation-runner.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const { SQLiteStore } = await import("../src/storage/index.js");

    class RecordingProvider {
      readonly name = "recording";
      prompts: string[] = [];

      defaultModel(): string {
        return "recording-model";
      }

      async complete(opts: { userPrompt: string }): Promise<{ text: string; model: string; usage: Record<string, number> }> {
        this.prompts.push(opts.userPrompt);

        if (opts.userPrompt.includes("Describe your strategy")) {
          return {
            text: JSON.stringify({ aggression: 0.60, defense: 0.55, path_bias: 0.50 }),
            model: "recording-model",
            usage: {},
          };
        }

        if (opts.userPrompt.includes("Analyze strengths/failures")) {
          return {
            text: "## Findings\n\n- Pressure is balanced.\n\n## Recommendations\n\n- Preserve defender coverage.",
            model: "recording-model",
            usage: {},
          };
        }

        return {
          text:
            "<!-- PLAYBOOK_START -->\n" +
            "## Strategy Updates\n\n- Prefer safer flank openings after early overextension.\n\n" +
            "<!-- PLAYBOOK_END -->\n\n" +
            "<!-- LESSONS_START -->\n" +
            "- Stable progress comes from balanced aggression and defense.\n" +
            "<!-- LESSONS_END -->\n\n" +
            "<!-- COMPETITOR_HINTS_START -->\n" +
            "- Keep defender coverage above 0.5.\n" +
            "<!-- COMPETITOR_HINTS_END -->",
          model: "recording-model",
          usage: {},
        };
      }
    }

    const provider = new RecordingProvider();
    const dbPath = join(dir, "test.db");
    const runsRoot = join(dir, "runs");
    const knowledgeRoot = join(dir, "knowledge");
    const store = new SQLiteStore(dbPath);
    store.migrate(join(__dirname, "..", "migrations"));

    const runner = new GenerationRunner({
      provider,
      scenario: new GridCtfScenario(),
      store,
      runsRoot,
      knowledgeRoot,
      matchesPerGeneration: 2,
      maxRetries: 0,
      minDelta: 0.0,
    });

    await runner.run("test-knowledge-loop", 2);

    const competitorPrompts = provider.prompts.filter((prompt) =>
      prompt.includes("Describe your strategy"),
    );
    expect(competitorPrompts).toHaveLength(2);
    expect(competitorPrompts[0]).toContain("Current Playbook:");
    expect(competitorPrompts[0]).toContain("No playbook yet");
    expect(competitorPrompts[1]).toContain("Prefer safer flank openings");
    expect(competitorPrompts[1]).toContain("## Score Trajectory");

    const playbookPath = join(knowledgeRoot, "grid_ctf", "playbook.md");
    expect(existsSync(playbookPath)).toBe(true);
    expect(readFileSync(playbookPath, "utf-8")).toContain("Prefer safer flank openings");

    const promptArtifactPath = join(
      runsRoot,
      "test-knowledge-loop",
      "generations",
      "gen_1",
      "competitor_prompt.md",
    );
    expect(existsSync(promptArtifactPath)).toBe(true);
    expect(readFileSync(promptArtifactPath, "utf-8")).toContain("Strategy Interface");

    const summaryPath = join(
      runsRoot,
      "test-knowledge-loop",
      "generations",
      "gen_2",
      "tournament_summary.json",
    );
    expect(existsSync(summaryPath)).toBe(true);
    expect(JSON.parse(readFileSync(summaryPath, "utf-8")).gate_decision).toBeDefined();

    const replayPath = join(
      runsRoot,
      "test-knowledge-loop",
      "generations",
      "gen_2",
      "replays",
      "grid_ctf_2.json",
    );
    expect(existsSync(replayPath)).toBe(true);
    expect(JSON.parse(readFileSync(replayPath, "utf-8")).timeline).toBeDefined();

    store.close();
  });

  it("persists only the final attempt when a generation retries", async () => {
    const { GenerationRunner } = await import("../src/loop/generation-runner.js");
    const { SQLiteStore } = await import("../src/storage/index.js");
    const { ResultSchema } = await import("../src/scenarios/game-interface.js");

    class FixedScoreScenario {
      readonly name = "fixed_score";

      describeRules(): string {
        return "Score is taken directly from the submitted strategy.";
      }

      describeStrategyInterface(): string {
        return "Return JSON with a numeric `score` field in [0,1].";
      }

      describeEvaluationCriteria(): string {
        return "Higher score is better.";
      }

      initialState(seed = 0): Record<string, unknown> {
        return { seed };
      }

      getObservation(): { narrative: string; state: Record<string, unknown>; constraints: string[] } {
        return { narrative: "fixed", state: {}, constraints: [] };
      }

      validateActions(
        _state: Record<string, unknown>,
        _playerId: string,
        actions: Record<string, unknown>,
      ): [boolean, string] {
        return typeof actions.score === "number" ? [true, "ok"] : [false, "missing score"];
      }

      step(state: Record<string, unknown>): Record<string, unknown> {
        return state;
      }

      isTerminal(): boolean {
        return true;
      }

      getResult(state: Record<string, unknown>) {
        return ResultSchema.parse({
          score: Number(state.score ?? 0),
          winner: Number(state.score ?? 0) >= 0.5 ? "challenger" : "incumbent",
          summary: `score ${Number(state.score ?? 0).toFixed(4)}`,
          replay: [{ score: Number(state.score ?? 0) }],
          metrics: { score: Number(state.score ?? 0) },
        });
      }

      replayToNarrative(replay: Array<Record<string, unknown>>): string {
        return JSON.stringify(replay);
      }

      renderFrame(state: Record<string, unknown>): Record<string, unknown> {
        return state;
      }

      enumerateLegalActions(): null {
        return null;
      }

      scoringDimensions(): null {
        return null;
      }

      executeMatch(strategy: Record<string, unknown>, _seed: number) {
        return ResultSchema.parse({
          score: Number(strategy.score ?? 0),
          winner: Number(strategy.score ?? 0) >= 0.5 ? "challenger" : "incumbent",
          summary: `score ${Number(strategy.score ?? 0).toFixed(4)}`,
          replay: [{ score: Number(strategy.score ?? 0) }],
          metrics: { score: Number(strategy.score ?? 0) },
        });
      }
    }

    class RetryThenAdvanceProvider {
      readonly name = "retry-provider";
      private competitorCount = 0;

      defaultModel(): string {
        return "retry-provider";
      }

      async complete(opts: { userPrompt: string }): Promise<{ text: string; model: string; usage: Record<string, number> }> {
        if (opts.userPrompt.includes("Describe your strategy")) {
          this.competitorCount += 1;
          if (this.competitorCount === 1) {
            return { text: JSON.stringify({ score: 0.9 }), model: "retry-provider", usage: {} };
          }
          if (this.competitorCount === 2) {
            return { text: JSON.stringify({ score: 0.9 }), model: "retry-provider", usage: {} };
          }
          return { text: JSON.stringify({ score: 0.96 }), model: "retry-provider", usage: {} };
        }

        if (opts.userPrompt.includes("Analyze strengths/failures")) {
          return { text: "## Findings\n\n- Retry happened.", model: "retry-provider", usage: {} };
        }

        return {
          text:
            "<!-- PLAYBOOK_START -->\nRetry-safe playbook\n<!-- PLAYBOOK_END -->\n\n" +
            "<!-- LESSONS_START -->\n- Keep iterating.\n<!-- LESSONS_END -->\n\n" +
            "<!-- COMPETITOR_HINTS_START -->\n- Try a slightly higher score.\n<!-- COMPETITOR_HINTS_END -->",
          model: "retry-provider",
          usage: {},
        };
      }
    }

    const dbPath = join(dir, "retry.db");
    const store = new SQLiteStore(dbPath);
    store.migrate(join(__dirname, "..", "migrations"));

    const runner = new GenerationRunner({
      provider: new RetryThenAdvanceProvider(),
      scenario: new FixedScoreScenario(),
      store,
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
      matchesPerGeneration: 3,
      maxRetries: 1,
      minDelta: 0.05,
    });

    await runner.run("retry-run", 2);

    const matches = store.getMatchesForRun("retry-run");
    expect(matches).toHaveLength(6);
    expect(matches.filter((match) => match.generation_index === 2)).toHaveLength(3);
    expect(matches.filter((match) => match.generation_index === 2).every((match) => match.score === 0.96)).toBe(true);

    const gen2Outputs = store.getAgentOutputs("retry-run", 2);
    expect(gen2Outputs.filter((row) => row.role === "competitor")).toHaveLength(1);
    expect(gen2Outputs.find((row) => row.role === "competitor")?.content).toContain("0.96");

    store.close();
  });

  it("runs curator, writes session reports, and dispatches notifications when advanced features are enabled", async () => {
    const { GenerationRunner } = await import("../src/loop/generation-runner.js");
    const { DeterministicProvider } = await import("../src/providers/deterministic.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const { SQLiteStore } = await import("../src/storage/index.js");
    const { CallbackNotifier } = await import("../src/notifications/index.js");

    const dbPath = join(dir, "advanced.db");
    const runsRoot = join(dir, "runs");
    const knowledgeRoot = join(dir, "knowledge");
    const store = new SQLiteStore(dbPath);
    store.migrate(join(__dirname, "..", "migrations"));

    const notifications: Array<Record<string, unknown>> = [];
    const runner = new GenerationRunner({
      provider: new DeterministicProvider(),
      scenario: new GridCtfScenario(),
      store,
      runsRoot,
      knowledgeRoot,
      matchesPerGeneration: 2,
      maxRetries: 0,
      minDelta: 0.0,
      curatorEnabled: true,
      curatorConsolidateEveryNGens: 1,
      notifier: new CallbackNotifier((event) => notifications.push(event as Record<string, unknown>)),
      notifyOn: "threshold_met,completion",
    });

    await runner.run("advanced-run", 2);

    const outputs = store.getAgentOutputs("advanced-run", 2);
    expect(outputs.some((row) => row.role === "curator")).toBe(true);
    expect(outputs.some((row) => row.role === "curator_consolidation")).toBe(true);

    const runReportPath = join(runsRoot, "advanced-run", "session_report.md");
    const knowledgeReportPath = join(
      knowledgeRoot,
      "grid_ctf",
      "session_reports",
      "advanced-run.md",
    );
    expect(existsSync(runReportPath)).toBe(true);
    expect(existsSync(knowledgeReportPath)).toBe(true);
    expect(readFileSync(runReportPath, "utf-8")).toContain("# Session Report");

    expect(notifications.some((event) => event.type === "threshold_met")).toBe(true);
    expect(notifications.some((event) => event.type === "completion")).toBe(true);

    store.close();
  });

  it("tracks dead ends and injects fresh-start guidance after stagnation", async () => {
    const { GenerationRunner } = await import("../src/loop/generation-runner.js");
    const { DeterministicProvider } = await import("../src/providers/deterministic.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const { SQLiteStore } = await import("../src/storage/index.js");

    const dbPath = join(dir, "stagnation.db");
    const runsRoot = join(dir, "runs");
    const knowledgeRoot = join(dir, "knowledge");
    const store = new SQLiteStore(dbPath);
    store.migrate(join(__dirname, "..", "migrations"));

    const runner = new GenerationRunner({
      provider: new DeterministicProvider(),
      scenario: new GridCtfScenario(),
      store,
      runsRoot,
      knowledgeRoot,
      matchesPerGeneration: 2,
      maxRetries: 0,
      minDelta: 5.0,
      deadEndTrackingEnabled: true,
      deadEndMaxEntries: 5,
      stagnationResetEnabled: true,
      stagnationRollbackThreshold: 2,
      stagnationPlateauWindow: 3,
      stagnationPlateauEpsilon: 0.0001,
    });

    await runner.run("stagnation-run", 3);

    const deadEndsPath = join(knowledgeRoot, "grid_ctf", "dead_ends.md");
    expect(existsSync(deadEndsPath)).toBe(true);
    expect(readFileSync(deadEndsPath, "utf-8")).toContain("### Dead End");

    const promptPath = join(
      runsRoot,
      "stagnation-run",
      "generations",
      "gen_3",
      "competitor_prompt.md",
    );
    expect(existsSync(promptPath)).toBe(true);
    const prompt = readFileSync(promptPath, "utf-8");
    expect(prompt).toContain("Fresh Start Guidance");
    expect(prompt).toContain("Avoid repeating these recent dead ends");

    store.close();
  });
});

// ---------------------------------------------------------------------------
// Task 22+23: CLI run command
// ---------------------------------------------------------------------------

describe("CLI run command", () => {
  it("help output includes 'run' command", async () => {
    const { execFileSync } = await import("node:child_process");
    const result = execFileSync(
      "npx",
      ["tsx", join(__dirname, "..", "src", "cli", "index.ts"), "--help"],
      { encoding: "utf-8", timeout: 10000 },
    );
    expect(result).toContain("run");
  });
});
