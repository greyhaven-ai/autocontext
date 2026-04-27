import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { AppSettingsSchema, getSettingEnvKeys } from "../src/config/index.js";

type AppSettingsContractField = {
  default: unknown;
  env?: string[];
  maximum?: number;
  minimum?: number;
  python: string;
  python_env?: string[];
  type: string;
  typescript: string;
  typescript_env?: string[];
  values?: string[];
};

type AppSettingsContract = {
  env_alias_policy: string;
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

function contractEnv(field: AppSettingsContractField, runtime: "python" | "typescript"): string[] {
  const runtimeEnv = runtime === "python" ? field.python_env : field.typescript_env;
  return runtimeEnv ?? field.env ?? [];
}

describe("AppSettings shared contract", () => {
  it("declares unique portable setting names for both runtimes", () => {
    const pythonNames = CONTRACT.fields.map((field) => field.python);
    const typeScriptNames = CONTRACT.fields.map((field) => field.typescript);

    expect(new Set(pythonNames).size).toBe(pythonNames.length);
    expect(new Set(typeScriptNames).size).toBe(typeScriptNames.length);
  });

  it("covers live shared settings used by both runtimes", () => {
    const pythonNames = CONTRACT.fields.map((field) => field.python);
    const typeScriptNames = CONTRACT.fields.map((field) => field.typescript);

    expect(pythonNames).toEqual(expect.arrayContaining([
      "browser_allowed_domains",
      "browser_enabled",
      "consultation_enabled",
      "generation_time_budget_seconds",
      "monitor_heartbeat_timeout",
    ]));
    expect(typeScriptNames).toEqual(expect.arrayContaining([
      "browserAllowedDomains",
      "browserEnabled",
      "consultationEnabled",
      "generationTimeBudgetSeconds",
      "monitorHeartbeatTimeout",
    ]));
  });

  it("keeps TypeScript defaults and env aliases aligned with the shared contract", () => {
    const defaults = AppSettingsSchema.parse({}) as Record<string, unknown>;

    for (const field of CONTRACT.fields) {
      expect(defaults[field.typescript], field.typescript).toEqual(field.default);
      expect(getSettingEnvKeys(field.typescript), field.typescript).toEqual(
        contractEnv(field, "typescript"),
      );
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
