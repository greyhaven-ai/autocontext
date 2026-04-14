import { describe, expect, it, vi } from "vitest";

import {
  executeExportTrainingDataCommandWorkflow,
  EXPORT_TRAINING_DATA_HELP_TEXT,
  planExportTrainingDataCommand,
  renderExportTrainingDataProgress,
} from "../src/cli/export-training-data-command-workflow.js";

describe("export-training-data command workflow", () => {
  it("exposes stable help text", () => {
    expect(EXPORT_TRAINING_DATA_HELP_TEXT).toContain("autoctx export-training-data");
    expect(EXPORT_TRAINING_DATA_HELP_TEXT).toContain("--run-id");
    expect(EXPORT_TRAINING_DATA_HELP_TEXT).toContain("--scenario");
    expect(EXPORT_TRAINING_DATA_HELP_TEXT).toContain("--include-matches");
  });

  it("requires run-id or scenario", () => {
    expect(() =>
      planExportTrainingDataCommand({
        "run-id": undefined,
        scenario: undefined,
        "all-runs": false,
        output: undefined,
        "include-matches": false,
        "kept-only": false,
      }),
    ).toThrow("Error: --run-id or --scenario is required");
  });

  it("requires all-runs with scenario-only export", () => {
    expect(() =>
      planExportTrainingDataCommand({
        "run-id": undefined,
        scenario: "grid_ctf",
        "all-runs": false,
        output: undefined,
        "include-matches": false,
        "kept-only": false,
      }),
    ).toThrow("Error: --all-runs is required with --scenario");
  });

  it("plans export-training-data options", () => {
    expect(
      planExportTrainingDataCommand({
        "run-id": "run-123",
        scenario: "grid_ctf",
        "all-runs": true,
        output: "/tmp/export.jsonl",
        "include-matches": true,
        "kept-only": true,
      }),
    ).toEqual({
      runId: "run-123",
      scenario: "grid_ctf",
      allRuns: true,
      output: "/tmp/export.jsonl",
      includeMatches: true,
      keptOnly: true,
    });
  });

  it("renders progress updates for start and generation phases", () => {
    expect(
      renderExportTrainingDataProgress({
        phase: "start",
        totalRuns: 3,
        runIndex: 0,
        runId: "run-1",
        scenario: "grid_ctf",
        recordsEmitted: 0,
      }),
    ).toBe("Scanning 3 run(s)...");

    expect(
      renderExportTrainingDataProgress({
        phase: "generation",
        totalRuns: 3,
        runIndex: 0,
        runId: "run-1",
        scenario: "grid_ctf",
        generationIndex: 2,
        recordsEmitted: 7,
      }),
    ).toBe("Processed run run-1 generation 2 (7 records)");
  });

  it("executes export-training-data to stdout with progress lines", () => {
    const exportTrainingData = vi.fn((_store, _artifacts, opts) => {
      opts.onProgress?.({
        phase: "start",
        totalRuns: 2,
        runIndex: 0,
        runId: "run-1",
        scenario: "grid_ctf",
        recordsEmitted: 0,
      });
      opts.onProgress?.({
        phase: "generation",
        totalRuns: 2,
        runIndex: 0,
        runId: "run-1",
        scenario: "grid_ctf",
        generationIndex: 1,
        recordsEmitted: 3,
      });
      return [
        { kind: "training", score: 0.8 },
        { kind: "match", score: 0.6 },
      ];
    });

    const result = executeExportTrainingDataCommandWorkflow({
      plan: {
        runId: "run-123",
        scenario: undefined,
        allRuns: false,
        output: undefined,
        includeMatches: true,
        keptOnly: false,
      },
      store: { kind: "store" },
      artifacts: { kind: "artifacts" },
      exportTrainingData,
    });

    expect(exportTrainingData).toHaveBeenCalledWith(
      { kind: "store" },
      { kind: "artifacts" },
      expect.objectContaining({
        runId: "run-123",
        scenario: undefined,
        includeMatches: true,
        keptOnly: false,
        onProgress: expect.any(Function),
      }),
    );
    expect(result.stderrLines).toEqual([
      "Exporting training data for run run-123...",
      "Scanning 2 run(s)...",
      "Processed run run-1 generation 1 (3 records)",
      "Exported 2 record(s).",
    ]);
    expect(result.stdout).toBe(
      ['{"kind":"training","score":0.8}', '{"kind":"match","score":0.6}'].join("\n"),
    );
  });

  it("writes export-training-data to a file and returns summary json", () => {
    const writeOutputFile = vi.fn();

    const result = executeExportTrainingDataCommandWorkflow({
      plan: {
        runId: undefined,
        scenario: "grid_ctf",
        allRuns: true,
        output: "/tmp/export.jsonl",
        includeMatches: false,
        keptOnly: true,
      },
      store: { kind: "store" },
      artifacts: { kind: "artifacts" },
      exportTrainingData: () => [{ kind: "training", score: 0.8 }],
      writeOutputFile,
    });

    expect(writeOutputFile).toHaveBeenCalledWith(
      "/tmp/export.jsonl",
      '{"kind":"training","score":0.8}\n',
    );
    expect(result.stdout).toBe(JSON.stringify({ output: "/tmp/export.jsonl", records: 1 }));
  });
});
