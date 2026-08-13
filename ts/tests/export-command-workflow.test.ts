import { describe, expect, it, vi } from "vitest";

import {
  executeExportCommandWorkflow,
  EXPORT_HELP_TEXT,
  normalizeExportFormat,
  planExportCommand,
} from "../src/cli/export-command-workflow.js";

describe("export command workflow", () => {
  it("exposes stable help text", () => {
    expect(EXPORT_HELP_TEXT).toContain("autoctx export");
    expect(EXPORT_HELP_TEXT).toContain("autoctx export <run-id>");
    expect(EXPORT_HELP_TEXT).toContain("--scenario");
    expect(EXPORT_HELP_TEXT).toContain("--format <format>");
    expect(EXPORT_HELP_TEXT).toContain("json or pi-package");
    expect(EXPORT_HELP_TEXT).toContain("JSON defaults to stdout");
    expect(EXPORT_HELP_TEXT).toContain("import-package");
  });

  it("normalizes the canonical formats and legacy strategy alias", () => {
    expect(normalizeExportFormat(undefined)).toBe("json");
    expect(normalizeExportFormat("JSON")).toBe("json");
    expect(normalizeExportFormat("pi-package")).toBe("pi-package");
    expect(normalizeExportFormat("strategy")).toBe("json");
    expect(() => normalizeExportFormat("hermes-skill")).toThrow(
      "--format must be one of json, pi-package",
    );
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
      format: "json",
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
      format: "json",
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
    expect(rendered).toBe("Exported grid_ctf package to /tmp/pkg.json");
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

    expect(JSON.parse(rendered)).toEqual({
      scenario: "grid_ctf",
      format: "json",
      output_path: "/tmp/pkg.json",
      best_score: 0,
      lessons_count: 0,
      harness_count: 0,
    });
  });

  it("writes Pi packages to the default directory and returns a structured receipt", () => {
    const writePiPackageOutput = vi.fn((_pkg, outputDir: string) => ({
      outputDir,
      files: [`${outputDir}/README.md`, `${outputDir}/package.json`],
    }));

    const rendered = executeExportCommandWorkflow({
      scenarioName: "grid_ctf",
      format: "pi-package",
      json: true,
      packageVersion: "0.15.0",
      exportStrategyPackage: () => ({
        scenario_name: "grid_ctf",
        display_name: "Grid CTF",
        description: "Capture the flag.",
        lessons: [],
        best_strategy: null,
        skill_markdown: "---\nname: grid-ctf\n---",
      }),
      artifacts: { kind: "artifacts" },
      store: { kind: "store" },
      writePiPackageOutput,
    });

    expect(writePiPackageOutput).toHaveBeenCalledWith(
      expect.objectContaining({ packageDirName: "grid-ctf-pi-package" }),
      "grid-ctf-pi-package",
    );
    expect(JSON.parse(rendered)).toEqual({
      scenario: "grid_ctf",
      format: "pi-package",
      output_path: "grid-ctf-pi-package",
      file_count: 2,
      files: ["README.md", "package.json"],
    });
  });
});
