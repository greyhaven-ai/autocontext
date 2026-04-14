import { describe, expect, it } from "vitest";

import { executeSimulateExportWorkflow } from "../src/cli/simulate-command-workflow.js";

describe("simulate export workflow", () => {
  it("rejects unsupported export formats", () => {
    expect(() =>
      executeSimulateExportWorkflow({
        exportId: "deploy_sim",
        format: "xml",
        knowledgeRoot: "/tmp/knowledge",
        json: false,
        exportSimulation: () => ({ status: "completed", format: "json", outputPath: "/tmp/export.json" }),
      }),
    ).toThrow("Export failed: Unsupported export format 'xml'. Use json, markdown, or csv.");
  });

  it("surfaces export failures from the exporter", () => {
    expect(() =>
      executeSimulateExportWorkflow({
        exportId: "missing_sim",
        format: "json",
        knowledgeRoot: "/tmp/knowledge",
        json: false,
        exportSimulation: () => ({ status: "failed", format: "json", error: "not found" }),
      }),
    ).toThrow("Export failed: not found");
  });

  it("renders json export results", () => {
    expect(
      executeSimulateExportWorkflow({
        exportId: "deploy_sim",
        format: "markdown",
        knowledgeRoot: "/tmp/knowledge",
        json: true,
        exportSimulation: (request: { id: string; knowledgeRoot: string; format: "json" | "markdown" | "csv" }) => {
          expect(request).toEqual({
            id: "deploy_sim",
            knowledgeRoot: "/tmp/knowledge",
            format: "markdown",
          });
          return { status: "completed", format: "markdown", outputPath: "/tmp/export.md" };
        },
      }),
    ).toBe(
      JSON.stringify(
        { status: "completed", format: "markdown", outputPath: "/tmp/export.md" },
        null,
        2,
      ),
    );
  });

  it("renders human-readable export success output", () => {
    expect(
      executeSimulateExportWorkflow({
        exportId: "deploy_sim",
        format: undefined,
        knowledgeRoot: "/tmp/knowledge",
        json: false,
        exportSimulation: () => ({ status: "completed", format: "json", outputPath: "/tmp/export.json" }),
      }),
    ).toBe("Exported: /tmp/export.json");
  });
});
