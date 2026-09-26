/**
 * AC-1048: the TypeScript loop cannot resume a run, so a caller-chosen run id
 * that already names a stored run is refused before anything is written.
 * Before the fix, run() re-ran generation 1 inside the stored run: it replaced
 * the generation row, added a second set of matches and agent outputs, wrote
 * another scenario's replay beside the old one and reopened terminal runs.
 */

import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { asDbPath, asRunId } from "../src/domain/ids.js";
import { HookBus, HookEvents } from "../src/extensions/index.js";
import { GenerationRunner } from "../src/loop/generation-runner.js";
import { DeterministicProvider } from "../src/providers/deterministic.js";
import type { ScenarioInterface } from "../src/scenarios/game-interface.js";
import { GridCtfScenario } from "../src/scenarios/grid-ctf.js";
import { OthelloScenario } from "../src/scenarios/othello.js";
import { SQLiteStore } from "../src/storage/index.js";

const MIGRATIONS = join(import.meta.dirname, "..", "migrations");
const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");

interface Fixture {
  store: SQLiteStore;
  runsRoot: string;
  knowledgeRoot: string;
}

function listFiles(dir: string): string[] {
  return existsSync(dir) ? readdirSync(dir, { recursive: true, encoding: "utf8" }).sort() : [];
}

function snapshot(fixture: Fixture, runId: string) {
  const generations = fixture.store.getGenerations(runId);
  return {
    run: fixture.store.getRun(runId),
    generations,
    matches: fixture.store.getMatchesForRun(runId),
    agentOutputs: generations.map((row) =>
      fixture.store.getAgentOutputs(runId, row.generation_index),
    ),
    runFiles: listFiles(fixture.runsRoot),
    knowledgeFiles: listFiles(fixture.knowledgeRoot),
  };
}

function recordRunStarts(): { hookBus: HookBus; runStarts: unknown[] } {
  const hookBus = new HookBus();
  const runStarts: unknown[] = [];
  hookBus.on(HookEvents.RUN_START, (event) => {
    runStarts.push(event.payload);
    return undefined;
  });
  return { hookBus, runStarts };
}

describe("GenerationRunner with an existing run id", () => {
  const roots: string[] = [];
  const stores: SQLiteStore[] = [];
  afterEach(() => {
    for (const store of stores.splice(0)) store.close();
    for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
  });

  function openFixture(): Fixture {
    const root = mkdtempSync(join(tmpdir(), "autoctx-existing-run-"));
    roots.push(root);
    const store = new SQLiteStore(asDbPath(join(root, "test.db")));
    stores.push(store);
    store.migrate(MIGRATIONS);
    return { store, runsRoot: join(root, "runs"), knowledgeRoot: join(root, "knowledge") };
  }

  function buildRunner(
    fixture: Fixture,
    scenario: ScenarioInterface,
    hookBus?: HookBus,
  ): GenerationRunner {
    return new GenerationRunner({
      provider: new DeterministicProvider(),
      agentProvider: "deterministic",
      scenario,
      store: fixture.store,
      runsRoot: fixture.runsRoot,
      knowledgeRoot: fixture.knowledgeRoot,
      matchesPerGeneration: 3,
      ...(hookBus ? { hookBus } : {}),
    });
  }

  async function seedGridCtfRun(fixture: Fixture, runId: string, generations: number) {
    await buildRunner(fixture, new GridCtfScenario()).run(asRunId(runId), generations);
  }

  it("refuses a run id that belongs to another scenario and writes nothing", async () => {
    const fixture = openFixture();
    await seedGridCtfRun(fixture, "tsdemo", 3);
    const before = snapshot(fixture, "tsdemo");
    expect(before.run).toMatchObject({
      scenario: "grid_ctf",
      target_generations: 3,
      status: "completed",
    });
    expect(before.matches).toHaveLength(9);
    const { hookBus, runStarts } = recordRunStarts();

    await expect(
      buildRunner(fixture, new OthelloScenario(), hookBus).run(asRunId("tsdemo"), 1),
    ).rejects.toThrow("run 'tsdemo' belongs to scenario 'grid_ctf', not 'othello'");

    expect(snapshot(fixture, "tsdemo")).toEqual(before);
    expect(listFiles(fixture.runsRoot).some((path) => path.endsWith("othello_1.json"))).toBe(false);
    expect(existsSync(join(fixture.knowledgeRoot, "othello"))).toBe(false);
    expect(runStarts).toEqual([]);
  });

  it("refuses a same-scenario loop run, since the TypeScript loop cannot resume", async () => {
    const fixture = openFixture();
    await seedGridCtfRun(fixture, "tssame", 3);
    const before = snapshot(fixture, "tssame");
    const { hookBus, runStarts } = recordRunStarts();

    await expect(
      buildRunner(fixture, new GridCtfScenario(), hookBus).run(asRunId("tssame"), 5),
    ).rejects.toThrow(
      "run 'tssame' already exists; continue it with the Python `autoctx resume` or use a new run id",
    );

    expect(snapshot(fixture, "tssame")).toEqual(before);
    expect(runStarts).toEqual([]);
  });

  it("refuses a stopped run and leaves it stopped", async () => {
    const fixture = openFixture();
    await seedGridCtfRun(fixture, "tsstop", 1);
    fixture.store.updateRunStatus("tsstop", "stopped");
    const before = snapshot(fixture, "tsstop");
    const { hookBus, runStarts } = recordRunStarts();

    await expect(
      buildRunner(fixture, new GridCtfScenario(), hookBus).run(asRunId("tsstop"), 1),
    ).rejects.toThrow("run 'tsstop' was stopped and is terminal; start a new run id to continue");

    expect(snapshot(fixture, "tsstop")).toEqual(before);
    expect(fixture.store.getRun("tsstop")?.status).toBe("stopped");
    expect(runStarts).toEqual([]);
  });

  it.each(["import", "agent_task", "artifact_editing"])(
    "refuses a run with executor_mode %s, which the loop did not write",
    async (executorMode) => {
      const fixture = openFixture();
      fixture.store.createRun("tsforeign", "othello", 1, executorMode);
      fixture.store.upsertGeneration("tsforeign", 1, {
        meanScore: 0.9,
        bestScore: 0.9,
        elo: 1000,
        wins: 0,
        losses: 0,
        gateDecision: "accepted",
        status: "completed",
      });
      fixture.store.updateRunStatus("tsforeign", "completed");
      const before = snapshot(fixture, "tsforeign");
      const { hookBus, runStarts } = recordRunStarts();

      await expect(
        buildRunner(fixture, new OthelloScenario(), hookBus).run(asRunId("tsforeign"), 2),
      ).rejects.toThrow(
        "run 'tsforeign' was not created by the generation loop and cannot be resumed",
      );

      expect(snapshot(fixture, "tsforeign")).toEqual(before);
      expect(runStarts).toEqual([]);
    },
  );
});

describe("autoctx run --run-id with an existing run id", () => {
  const roots: string[] = [];
  afterEach(() => {
    for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
  });

  it("exits non-zero with the refusal on stderr and leaves the run untouched", () => {
    const dir = mkdtempSync(join(tmpdir(), "autoctx-existing-run-cli-"));
    roots.push(dir);
    mkdirSync(join(dir, "runs"));
    mkdirSync(join(dir, "knowledge"));
    writeFileSync(
      join(dir, ".autoctx.json"),
      JSON.stringify({
        provider: "deterministic",
        runs_dir: "./runs",
        knowledge_dir: "./knowledge",
      }),
    );
    const dbPath = asDbPath(join(dir, "runs", "autocontext.sqlite3"));
    const seed = new SQLiteStore(dbPath);
    seed.migrate(MIGRATIONS);
    seed.createRun("tsdemo", "grid_ctf", 3, "local", "deterministic");
    seed.updateRunStatus("tsdemo", "completed");
    const before = seed.getRun("tsdemo");
    seed.close();

    const env: NodeJS.ProcessEnv = { ...process.env, NODE_NO_WARNINGS: "1" };
    for (const key of Object.keys(env)) {
      if (key.startsWith("AUTOCONTEXT_") || key.endsWith("_API_KEY")) delete env[key];
    }
    const result = spawnSync(
      "npx",
      ["tsx", CLI, "run", "othello", "--run-id", "tsdemo", "--iterations", "1", "--json"],
      { cwd: dir, env, encoding: "utf8", timeout: 60_000 },
    );

    expect(result.status).toBe(1);
    expect(result.stdout).toBe("");
    const lastStderrLine = result.stderr.trim().split("\n").at(-1) ?? "";
    expect(JSON.parse(lastStderrLine)).toEqual({
      error: "run 'tsdemo' belongs to scenario 'grid_ctf', not 'othello'",
    });
    const after = new SQLiteStore(dbPath);
    try {
      expect(after.getRun("tsdemo")).toEqual(before);
      expect(after.getGenerations("tsdemo")).toEqual([]);
    } finally {
      after.close();
    }
    expect(existsSync(join(dir, "knowledge", "othello"))).toBe(false);
  }, 90_000);
});
