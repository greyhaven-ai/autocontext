import { describe, expect, it, vi } from "vitest";

import {
  executeImportPackageCommandWorkflow,
  IMPORT_PACKAGE_HELP_TEXT,
  planImportPackageCommand,
} from "../src/cli/import-package-command-workflow.js";

describe("import-package command workflow", () => {
  it("exposes stable help text", () => {
    expect(IMPORT_PACKAGE_HELP_TEXT).toContain("autoctx import-package");
    expect(IMPORT_PACKAGE_HELP_TEXT).toContain("--file");
    expect(IMPORT_PACKAGE_HELP_TEXT).toContain("overwrite|merge|skip");
  });

  it("requires a package file", () => {
    expect(() =>
      planImportPackageCommand({
        file: undefined,
        scenario: undefined,
        conflict: undefined,
        json: false,
      }),
    ).toThrow("Error: --file is required");
  });

  it("rejects unsupported conflict policies", () => {
    expect(() =>
      planImportPackageCommand({
        file: "/tmp/package.json",
        scenario: undefined,
        conflict: "replace",
        json: true,
      }),
    ).toThrow("Error: --conflict must be one of overwrite, merge, skip");
  });

  it("plans import-package with defaults", () => {
    expect(
      planImportPackageCommand({
        file: "/tmp/package.json",
        scenario: "grid_ctf",
        conflict: undefined,
        json: false,
      }),
    ).toEqual({
      file: "/tmp/package.json",
      scenarioOverride: "grid_ctf",
      conflictPolicy: "overwrite",
      json: false,
    });
  });

  it("parses raw packages and renders import results as json", () => {
    const importStrategyPackage = vi.fn(() => ({
      scenario: "grid_ctf",
      playbookWritten: true,
      harnessWritten: ["validator"],
      harnessSkipped: [],
      skillWritten: true,
      metadataWritten: true,
      conflictPolicy: "overwrite",
    }));

    const rendered = executeImportPackageCommandWorkflow({
      rawPackage: '{"scenario_name":"grid_ctf"}',
      skillsRoot: "/tmp/skills",
      scenarioOverride: "grid_ctf_override",
      conflictPolicy: "merge",
      artifacts: { kind: "artifacts" },
      importStrategyPackage,
    });

    expect(importStrategyPackage).toHaveBeenCalledWith({
      rawPackage: { scenario_name: "grid_ctf" },
      artifacts: { kind: "artifacts" },
      skillsRoot: "/tmp/skills",
      scenarioOverride: "grid_ctf_override",
      conflictPolicy: "merge",
    });
    expect(JSON.parse(rendered)).toEqual({
      scenario: "grid_ctf",
      playbookWritten: true,
      harnessWritten: ["validator"],
      harnessSkipped: [],
      skillWritten: true,
      metadataWritten: true,
      conflictPolicy: "overwrite",
    });
  });
});
