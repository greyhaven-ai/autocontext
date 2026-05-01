import { describe, expect, it } from "vitest";

import {
  planWorkerCommand,
  renderWorkerResult,
  WORKER_HELP_TEXT,
} from "../src/cli/worker-command-workflow.js";

describe("worker command workflow", () => {
  it("plans daemon defaults and once-mode overrides", () => {
    expect(planWorkerCommand({})).toEqual({
      pollInterval: 60,
      concurrency: 1,
      maxEmptyPolls: 0,
      model: undefined,
      once: false,
      json: false,
    });

    expect(planWorkerCommand({
      "poll-interval": "0.25",
      concurrency: "3",
      "max-empty-polls": "1",
      model: "override-model",
      once: true,
      json: true,
    })).toEqual({
      pollInterval: 0.25,
      concurrency: 3,
      maxEmptyPolls: 1,
      model: "override-model",
      once: true,
      json: true,
    });
  });

  it("validates daemon command values before the CLI touches providers", () => {
    expect(() => planWorkerCommand({ "poll-interval": "-1" })).toThrow(
      "poll interval must be non-negative",
    );
    expect(() => planWorkerCommand({ concurrency: "0" })).toThrow(
      "concurrency must be a positive integer",
    );
    expect(() => planWorkerCommand({ "max-empty-polls": "-1" })).toThrow(
      "max empty polls must be zero or a positive integer",
    );
  });

  it("renders JSON and human results", () => {
    expect(renderWorkerResult({
      mode: "once",
      tasksProcessed: 2,
      pollInterval: 0.25,
      concurrency: 3,
      json: true,
    })).toBe(JSON.stringify({
      status: "stopped",
      mode: "once",
      tasksProcessed: 2,
      pollInterval: 0.25,
      concurrency: 3,
    }));

    expect(renderWorkerResult({
      mode: "daemon",
      tasksProcessed: 1,
      pollInterval: 60,
      concurrency: 1,
      json: false,
    })).toContain("Processed 1 task");
  });

  it("documents persistent worker options", () => {
    expect(WORKER_HELP_TEXT).toContain("autoctx worker");
    expect(WORKER_HELP_TEXT).toContain("--poll-interval");
    expect(WORKER_HELP_TEXT).toContain("--max-empty-polls");
  });
});
