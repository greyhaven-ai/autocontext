import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { RunManager } from "../src/server/run-manager.js";
import { SQLiteStore } from "../src/storage/index.js";
import { asDbPath } from "../src/domain/ids.js";

describe("deterministic structured task smoke", () => {
  let dir: string;
  let previousAgentProvider: string | undefined;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ac-structured-smoke-"));
    previousAgentProvider = process.env.AUTOCONTEXT_AGENT_PROVIDER;
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "deterministic";
  });

  afterEach(() => {
    if (previousAgentProvider === undefined) {
      delete process.env.AUTOCONTEXT_AGENT_PROVIDER;
    } else {
      process.env.AUTOCONTEXT_AGENT_PROVIDER = previousAgentProvider;
    }
    rmSync(dir, { recursive: true, force: true });
  });

  it("creates, confirms, and completes a two-round task with retained results", async () => {
    const dbPath = join(dir, "autocontext.db");
    const manager = new RunManager({
      dbPath,
      migrationsDir: join(import.meta.dirname, "..", "migrations"),
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
      providerType: "deterministic",
    });
    const eventNames: string[] = [];
    const terminal = new Promise<string>((resolve, reject) => {
      const timeout = setTimeout(
        () => reject(new Error("Timed out waiting for deterministic task run")),
        5_000,
      );
      manager.subscribeEvents((event) => {
        eventNames.push(event);
        if (event === "run_completed" || event === "run_failed") {
          clearTimeout(timeout);
          resolve(event);
        }
      });
    });

    const preview = await manager.createTask(
      {
        schemaVersion: 1,
        objective: "Analyze the observation and recommend the highest-value next step.",
        target: "The current onboarding analysis.",
        deliverable: {
          description: "A concise finding and actionable recommendation.",
          outputFormat: "free_text",
        },
        dataSources: [],
        criteria: "Score task completion and actionability.",
        qualityThreshold: 0.9,
        iterations: 2,
        revisionPrompt: null,
      },
      [],
    );
    expect(preview.name).toBeTruthy();

    const ready = await manager.confirmScenario();
    const runId = await manager.startRun(ready.name, 2, "structured-smoke-run");
    expect(runId).toBe("structured-smoke-run");
    expect(await terminal).toBe("run_completed");
    await waitFor(() => !manager.isActive);

    expect(eventNames).toContain("generation_completed");
    expect(eventNames).toContain("action_detail");
    expect(eventNames).not.toContain("run_failed");

    const store = new SQLiteStore(asDbPath(dbPath));
    try {
      expect(store.getRun(runId)).toMatchObject({
        status: "completed",
        agent_provider: "deterministic",
      });
      expect(store.getGenerations(runId)).toHaveLength(2);
      expect(store.getAgentOutputs(runId, 2)).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            role: "competitor",
            content: expect.stringContaining("Deterministic revised task result"),
          }),
        ]),
      );
    } finally {
      store.close();
    }
  });
});

async function waitFor(predicate: () => boolean, timeoutMs = 5_000): Promise<void> {
  const startedAt = Date.now();
  while (!predicate()) {
    if (Date.now() - startedAt > timeoutMs) {
      throw new Error("Timed out waiting for run manager to become idle");
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}
