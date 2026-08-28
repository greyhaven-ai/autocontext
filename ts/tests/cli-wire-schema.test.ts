import Ajv2020 from "ajv/dist/2020.js";
import type { AnySchema } from "ajv";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { renderStatusResult } from "../src/cli/queue-status-command-workflow.js";
import {
  renderRunShow,
  renderRunStatusJsonLine,
  type RunInspectionGeneration,
  type RunInspectionRun,
} from "../src/cli/run-inspection-command-workflow.js";

const DOCS_ROOT = resolve(import.meta.dirname, "..", "..", "docs");
const SCHEMA_ROOT = resolve(DOCS_ROOT, "cli-schemas");
const FIXTURE_ROOT = resolve(DOCS_ROOT, "cli-fixtures");

function readJson<T>(path: string): T {
  return JSON.parse(readFileSync(path, "utf-8")) as T;
}

const fixture = <T>(name: string) => readJson<T>(resolve(FIXTURE_ROOT, name));

describe("shared CLI wire fixtures", () => {
  it("conform to their versioned schemas", () => {
    const ajv = new Ajv2020({ allErrors: true, strict: true });
    for (const name of [
      "run-status-v1.schema.json",
      "run-show-v1.schema.json",
      "queue-status-v1.schema.json",
      "strategy-package-v1.schema.json",
    ]) {
      ajv.addSchema(readJson<AnySchema>(resolve(SCHEMA_ROOT, name)));
    }

    for (const [schemaId, fixtureName] of [
      ["run-status-v1.schema.json", "run-status-v1.json"],
      ["run-show-v1.schema.json", "run-show-v1.json"],
      ["queue-status-v1.schema.json", "queue-status-v1.json"],
      ["strategy-package-v1.schema.json", "strategy-package-v1.json"],
    ]) {
      const validate = ajv.getSchema(schemaId);
      expect(validate, schemaId).toBeDefined();
      expect(validate?.(fixture<Record<string, unknown>>(fixtureName)), JSON.stringify(validate?.errors)).toBe(true);
    }
  });

  it("matches TypeScript status, show, and queue renderers", () => {
    const status = fixture<{
      run: RunInspectionRun;
      latest_generation: RunInspectionGeneration;
    }>("run-status-v1.json");
    const show = fixture<{
      run: RunInspectionRun;
      generation: RunInspectionGeneration;
    }>("run-show-v1.json");
    const queue = fixture<Record<string, unknown>>("queue-status-v1.json");

    expect(JSON.parse(renderRunStatusJsonLine(status.run, [status.latest_generation]))).toEqual(status);
    expect(JSON.parse(renderRunShow(show.run, [show.generation], { json: true }))).toEqual(show);
    expect(JSON.parse(renderStatusResult({ pendingCount: 3 }))).toEqual(queue);
  });

  it("omits the default minimum while exposing an opted-in floor", () => {
    const status = fixture<{
      run: RunInspectionRun;
      latest_generation: RunInspectionGeneration;
    }>("run-status-v1.json");
    const defaultRun = { ...status.run, minimum_generations: 1 };
    const optedInRun = { ...status.run, minimum_generations: 2 };

    expect(JSON.parse(renderRunStatusJsonLine(defaultRun, [status.latest_generation])).run)
      .not.toHaveProperty("minimum_generations");
    expect(JSON.parse(renderRunStatusJsonLine(optedInRun, [status.latest_generation])).run)
      .toHaveProperty("minimum_generations", 2);
  });
});
