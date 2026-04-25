import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { AppSettingsSchema, getSettingEnvKeys } from "../src/config/index.js";

type AppSettingsContractField = {
  default: unknown;
  env: string[];
  maximum?: number;
  minimum?: number;
  python: string;
  type: string;
  typescript: string;
  values?: string[];
};

type AppSettingsContract = {
  fields: AppSettingsContractField[];
  unknown_field_policy: "ignore";
  version: number;
};

const CONTRACT = JSON.parse(
  readFileSync(
    join(import.meta.dirname, "..", "..", "docs", "app-settings-contract.json"),
    "utf-8",
  ),
) as AppSettingsContract;

describe("AppSettings shared contract", () => {
  it("declares unique portable setting names for both runtimes", () => {
    const pythonNames = CONTRACT.fields.map((field) => field.python);
    const typeScriptNames = CONTRACT.fields.map((field) => field.typescript);

    expect(new Set(pythonNames).size).toBe(pythonNames.length);
    expect(new Set(typeScriptNames).size).toBe(typeScriptNames.length);
  });

  it("keeps TypeScript defaults and env aliases aligned with the shared contract", () => {
    const defaults = AppSettingsSchema.parse({}) as Record<string, unknown>;

    for (const field of CONTRACT.fields) {
      expect(defaults[field.typescript], field.typescript).toEqual(field.default);
      expect(getSettingEnvKeys(field.typescript), field.typescript).toEqual(field.env);
    }
  });

  it("ignores unknown fields consistently with the shared contract", () => {
    expect(CONTRACT.unknown_field_policy).toBe("ignore");

    const parsed = AppSettingsSchema.parse({
      notAPortableSetting: "ignored",
    }) as Record<string, unknown>;

    expect(parsed.notAPortableSetting).toBeUndefined();
  });

  it("rejects representative invalid shared setting values", () => {
    const invalidCases: Array<{ field: string; value: unknown }> = [
      { field: "matchesPerGeneration", value: 0 },
      { field: "claudeTimeout", value: 0 },
      { field: "browserProfileMode", value: "shared" },
      { field: "monitorMaxConditions", value: 0 },
    ];

    for (const invalidCase of invalidCases) {
      expect(
        () => AppSettingsSchema.parse({ [invalidCase.field]: invalidCase.value }),
        invalidCase.field,
      ).toThrow();
    }
  });
});
