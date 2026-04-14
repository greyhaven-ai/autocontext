import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { describe, expect, it, vi } from "vitest";

import {
  readReplayArtifact,
  registerFeedbackReplayTools,
} from "../src/mcp/feedback-replay-tools.js";

function createFakeServer() {
  const registeredTools: Record<
    string,
    {
      description: string;
      schema: Record<string, unknown>;
      handler: (args: Record<string, unknown>) => Promise<{ content: Array<{ type: string; text: string }> }>;
    }
  > = {};

  return {
    registeredTools,
    tool(
      name: string,
      description: string,
      schema: Record<string, unknown>,
      handler: (args: Record<string, unknown>) => Promise<{ content: Array<{ type: string; text: string }> }>,
    ) {
      registeredTools[name] = { description, schema, handler };
    },
  };
}

describe("feedback and replay MCP tools", () => {
  it("records and retrieves feedback with stable payload shapes", async () => {
    const server = createFakeServer();
    const insertHumanFeedback = vi.fn(() => 42);
    const getHumanFeedback = vi.fn(() => [
      {
        id: 42,
        scenario_name: "grid_ctf",
        generation_id: null,
        agent_output: "{\"aggression\":0.6}",
        human_score: 0.8,
        human_notes: "Strong opening.",
        created_at: "2026-04-10 00:00:00",
      },
    ]);

    registerFeedbackReplayTools(server, {
      store: {
        insertHumanFeedback,
        getHumanFeedback,
      },
      runsRoot: "/runs",
    });

    const inserted = await server.registeredTools.record_feedback.handler({
      scenario: "grid_ctf",
      agentOutput: "{\"aggression\":0.6}",
      score: 0.8,
      notes: "Strong opening.",
    });
    expect(JSON.parse(inserted.content[0].text)).toEqual({
      feedbackId: 42,
      scenario: "grid_ctf",
    });

    const fetched = await server.registeredTools.get_feedback.handler({
      scenario: "grid_ctf",
      limit: 5,
    });
    expect(JSON.parse(fetched.content[0].text)).toEqual([
      {
        id: 42,
        scenario_name: "grid_ctf",
        generation_id: null,
        agent_output: "{\"aggression\":0.6}",
        human_score: 0.8,
        human_notes: "Strong opening.",
        created_at: "2026-04-10 00:00:00",
      },
    ]);
    expect(getHumanFeedback).toHaveBeenCalledWith("grid_ctf", 5);
  });

  it("returns replay payloads through the injected replay reader", async () => {
    const server = createFakeServer();
    const readReplay = vi.fn(() => ({
      scenario: "grid_ctf",
      narrative: "Blue team secured the center route.",
    }));

    registerFeedbackReplayTools(server, {
      store: {
        insertHumanFeedback: vi.fn(),
        getHumanFeedback: vi.fn(),
      },
      runsRoot: "/runs",
      internals: {
        readReplayArtifact: readReplay,
      },
    });

    const replay = await server.registeredTools.run_replay.handler({
      runId: "run-1",
      generation: 1,
    });

    expect(readReplay).toHaveBeenCalledWith("/runs", "run-1", 1);
    expect(JSON.parse(replay.content[0].text)).toEqual({
      scenario: "grid_ctf",
      narrative: "Blue team secured the center route.",
    });
  });
});

describe("readReplayArtifact", () => {
  it("returns stable errors for missing replay directories and empty replay sets", () => {
    const tempDir = mkdtempSync(join(tmpdir(), "ac-replay-artifact-"));
    try {
      expect(readReplayArtifact(tempDir, "run-missing", 1)).toEqual({
        error: `no replay directory for run=run-missing gen=1`,
      });

      const replayDir = join(tempDir, "run-1", "generations", "gen_1", "replays");
      mkdirSync(replayDir, { recursive: true });
      expect(readReplayArtifact(tempDir, "run-1", 1)).toEqual({
        error: `no replay files under ${replayDir}`,
      });
    } finally {
      rmSync(tempDir, { recursive: true, force: true });
    }
  });

  it("loads the first sorted replay artifact payload", () => {
    const tempDir = mkdtempSync(join(tmpdir(), "ac-replay-artifact-"));
    try {
      const replayDir = join(tempDir, "run-1", "generations", "gen_1", "replays");
      mkdirSync(replayDir, { recursive: true });
      writeFileSync(
        join(replayDir, "b.json"),
        JSON.stringify({ scenario: "later" }),
        "utf-8",
      );
      writeFileSync(
        join(replayDir, "a.json"),
        JSON.stringify({ scenario: "earlier", generation: 1 }),
        "utf-8",
      );

      expect(readReplayArtifact(tempDir, "run-1", 1)).toEqual({
        scenario: "earlier",
        generation: 1,
      });
    } finally {
      rmSync(tempDir, { recursive: true, force: true });
    }
  });
});
