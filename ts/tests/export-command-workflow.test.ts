import { describe, expect, it, vi } from "vitest";

import {
  executeExportCommandWorkflow,
  EXPORT_HELP_TEXT,
  planExportCommand,
} from "../src/cli/export-command-workflow.js";

describe("export command workflow", () => {
  it("exposes stable help text", () => {
    expect(EXPORT_HELP_TEXT).toContain("autoctx export");
    expect(EXPORT_HELP_TEXT).toContain("autoctx export <run-id>");
    expect(EXPORT_HELP_TEXT).toContain("--scenario");
    expect(EXPORT_HELP_TEXT).toContain("import-package");
  });

  it("requires a scenario after resolution", async () => {
    await expect(
      planExportCommand(
        { scenario: undefined, output: undefined, json: false },
        async () => undefined,
        async () => undefined,
      ),
    ).rejects.toThrow("Error: --scenario or <run-id> is required");
  });

  it("plans export with resolved scenario and output options", async () => {
    await expect(
      planExportCommand(
        { scenario: "grid_ctf", output: "/tmp/pkg.json", json: true },
        async (value: string | undefined) => `${value}_resolved`,
        async () => undefined,
      ),
    ).resolves.toEqual({
      scenarioName: "grid_ctf_resolved",
      runId: undefined,
      output: "/tmp/pkg.json",
      json: true,
    });
  });

  it("plans export from a positional run id", async () => {
    await expect(
      planExportCommand(
        { positionals: ["run-123"], json: true },
        async () => undefined,
        async (runId: string) => (runId === "run-123" ? "grid_ctf" : undefined),
      ),
    ).resolves.toEqual({
      scenarioName: "grid_ctf",
      runId: "run-123",
      output: undefined,
      json: true,
    });
  });

  it("prefers precise scenario flags over positional run ids", async () => {
    await expect(
      planExportCommand(
        { scenario: "support_triage", positionals: ["run-123"] },
        async (scenario: string | undefined) => `${scenario}_resolved`,
        async () => "grid_ctf",
      ),
    ).resolves.toMatchObject({
      scenarioName: "support_triage_resolved",
      runId: undefined,
    });
  });

  it("keeps a named run id when paired with an explicit scenario", async () => {
    await expect(
      planExportCommand(
        { scenario: "grid_ctf", "run-id": "run-123" },
        async (scenario: string | undefined) => scenario,
        async (runId: string) => (runId === "run-123" ? "grid_ctf" : undefined),
      ),
    ).resolves.toMatchObject({
      scenarioName: "grid_ctf",
      runId: "run-123",
    });
  });

  it("renders package json to stdout when no output file is requested", () => {
    const exportStrategyPackage = vi.fn(() => ({ scenario_name: "grid_ctf", best_score: 0.83 }));

    const rendered = executeExportCommandWorkflow({
      scenarioName: "grid_ctf",
      runId: "run-123",
      exportStrategyPackage,
      artifacts: { kind: "artifacts" },
      store: { kind: "store" },
    });

    expect(exportStrategyPackage).toHaveBeenCalledWith({
      scenarioName: "grid_ctf",
      sourceRunId: "run-123",
      artifacts: { kind: "artifacts" },
      store: { kind: "store" },
    });
    expect(rendered).toBe(
      JSON.stringify({ scenario_name: "grid_ctf", best_score: 0.83 }, null, 2),
    );
  });

  it("writes export packages to files and returns human-readable output by default", () => {
    const writeOutputFile = vi.fn();

    const rendered = executeExportCommandWorkflow({
      scenarioName: "grid_ctf",
      output: "/tmp/pkg.json",
      json: false,
      exportStrategyPackage: () => ({ scenario_name: "grid_ctf" }),
      artifacts: { kind: "artifacts" },
      store: { kind: "store" },
      writeOutputFile,
    });

    expect(writeOutputFile).toHaveBeenCalledWith(
      "/tmp/pkg.json",
      `${JSON.stringify({ scenario_name: "grid_ctf" }, null, 2)}\n`,
    );
    expect(rendered).toBe("Exported to /tmp/pkg.json");
  });

  it("writes export packages to files and returns json output when requested", () => {
    const writeOutputFile = vi.fn();

    const rendered = executeExportCommandWorkflow({
      scenarioName: "grid_ctf",
      output: "/tmp/pkg.json",
      json: true,
      exportStrategyPackage: () => ({ scenario_name: "grid_ctf" }),
      artifacts: { kind: "artifacts" },
      store: { kind: "store" },
      writeOutputFile,
    });

    expect(rendered).toBe(JSON.stringify({ output: "/tmp/pkg.json" }));
  });
});
