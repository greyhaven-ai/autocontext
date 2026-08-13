import { describe, expect, it } from "vitest";
import Ajv2020 from "ajv/dist/2020.js";
import type { AnySchema } from "ajv";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { executeExportCommandWorkflow } from "../src/cli/export-command-workflow.js";

const SCHEMA_ROOT = resolve(import.meta.dirname, "..", "..", "docs", "cli-schemas");
const ajv = new Ajv2020({ allErrors: true, strict: true });

function validator(name: string) {
  const schema = JSON.parse(readFileSync(resolve(SCHEMA_ROOT, name), "utf-8")) as AnySchema;
  return ajv.compile(schema);
}

function strategyPackage(): Record<string, unknown> {
  return {
    format_version: 1,
    scenario_name: "grid_ctf",
    display_name: "Grid CTF",
    description: "Capture the flag.",
    playbook: "Scout first.",
    lessons: ["Prefer short routes."],
    best_strategy: { aggression: 0.7 },
    best_score: 0.88,
    best_elo: 1710,
    hints: "Watch borders.",
    harness: {},
    metadata: {},
    skill_markdown: "---\nname: grid-ctf\n---",
  };
}

describe("CLI export wire schemas", () => {
  it("accepts the shared strategy-package output", () => {
    const validate = validator("strategy-package-v1.schema.json");
    const output = executeExportCommandWorkflow({
      scenarioName: "grid_ctf",
      exportStrategyPackage: strategyPackage,
      artifacts: {},
      store: {},
    });

    expect(validate(JSON.parse(output)), JSON.stringify(validate.errors)).toBe(true);
  });

  it("accepts JSON-file and Pi-package receipts", () => {
    const validate = validator("export-receipt-v1.schema.json");
    const jsonReceipt = executeExportCommandWorkflow({
      scenarioName: "grid_ctf",
      output: "/tmp/grid-ctf.json",
      json: true,
      exportStrategyPackage: strategyPackage,
      artifacts: {},
      store: {},
      writeOutputFile: () => undefined,
    });
    const piReceipt = executeExportCommandWorkflow({
      scenarioName: "grid_ctf",
      format: "pi-package",
      output: "/tmp/grid-ctf-pi-package",
      json: true,
      packageVersion: "0.15.0",
      exportStrategyPackage: strategyPackage,
      artifacts: {},
      store: {},
      writePiPackageOutput: (_pkg, outputDir) => ({
        outputDir,
        files: [`${outputDir}/README.md`],
      }),
    });

    expect(validate(JSON.parse(jsonReceipt)), JSON.stringify(validate.errors)).toBe(true);
    expect(validate(JSON.parse(piReceipt)), JSON.stringify(validate.errors)).toBe(true);
  });

  it("accepts the structured error envelope", () => {
    const validate = validator("cli-error-v1.schema.json");
    expect(validate({ error: "--format must be one of json, pi-package" })).toBe(true);
  });
});
