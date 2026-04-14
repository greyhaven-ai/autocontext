import { describe, expect, it } from "vitest";

import {
  executeReplayCommandWorkflow,
  planReplayCommand,
  REPLAY_HELP_TEXT,
} from "../src/cli/replay-command-workflow.js";

describe("replay command workflow", () => {
  it("exposes stable help text", () => {
    expect(REPLAY_HELP_TEXT).toContain("autoctx replay");
    expect(REPLAY_HELP_TEXT).toContain("--run-id");
    expect(REPLAY_HELP_TEXT).toContain("--generation");
  });

  it("requires a run id", () => {
    expect(() => planReplayCommand({ "run-id": undefined, generation: undefined })).toThrow(
      "Error: --run-id is required",
    );
  });

  it("plans replay command values with default generation", () => {
    expect(planReplayCommand({ "run-id": "run-123", generation: undefined })).toEqual({
      runId: "run-123",
      generation: 1,
    });
  });

  it("fails with available generations when replay files are missing", () => {
    expect(() =>
      executeReplayCommandWorkflow({
        runId: "run-123",
        generation: 2,
        runsRoot: "/tmp/runs",
        existsSync: (path: string) => path === "/tmp/runs/run-123/generations",
        readdirSync: (path: string) => {
          if (path === "/tmp/runs/run-123/generations") {
            return ["gen_1", "gen_3"];
          }
          return [];
        },
        readFileSync: () => "{}",
      }),
    ).toThrow(
      "No replay files found under /tmp/runs/run-123/generations/gen_2/replays. Available generations: 1, 3.",
    );
  });

  it("returns stderr note and stdout payload for successful replay", () => {
    expect(
      executeReplayCommandWorkflow({
        runId: "run-123",
        generation: 2,
        runsRoot: "/tmp/runs",
        existsSync: (path: string) =>
          path === "/tmp/runs/run-123/generations"
          || path === "/tmp/runs/run-123/generations/gen_2/replays",
        readdirSync: (path: string) => {
          if (path === "/tmp/runs/run-123/generations") {
            return ["gen_1", "gen_2"];
          }
          if (path === "/tmp/runs/run-123/generations/gen_2/replays") {
            return ["b.json", "a.json"];
          }
          return [];
        },
        readFileSync: (path: string) => {
          expect(path).toBe("/tmp/runs/run-123/generations/gen_2/replays/a.json");
          return '{"scenario":"grid_ctf","winner":"blue"}';
        },
      }),
    ).toEqual({
      stderr: "Replaying generation 2. Available generations: 1, 2",
      stdout: JSON.stringify({ scenario: "grid_ctf", winner: "blue" }, null, 2),
    });
  });
});
