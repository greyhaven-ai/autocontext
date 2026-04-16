import { describe, expect, it } from "vitest";

import { AppSettingsSchema } from "../src/config/app-settings-schema.js";
import {
  buildSettingsAssemblyInput,
  getDefaultSettingsRecord,
  parseAppSettings,
} from "../src/config/settings-assembly-workflow.js";

describe("settings assembly workflow", () => {
  it("exposes the same default settings record as the schema", () => {
    expect(getDefaultSettingsRecord()).toEqual(AppSettingsSchema.parse({}));
  });

  it("assembles preset, project-config, and env overrides with env taking precedence", () => {
    const input = buildSettingsAssemblyInput({
      presetName: "quick",
      projectConfig: {
        provider: "ollama",
        model: "llama3.2",
        knowledgeDir: "/tmp/knowledge",
        runsDir: "/tmp/runs",
        dbPath: "/tmp/runs/db.sqlite3",
        gens: 4,
      },
      env: {
        AUTOCONTEXT_AGENT_PROVIDER: "deterministic",
        AUTOCONTEXT_MODEL_ANALYST: "analyst-model",
        AUTOCONTEXT_PI_NO_CONTEXT_FILES: "true",
      },
      defaults: getDefaultSettingsRecord(),
    });

    expect(input).toMatchObject({
      agentProvider: "deterministic",
      modelCompetitor: "llama3.2",
      modelAnalyst: "analyst-model",
      knowledgeRoot: "/tmp/knowledge",
      runsRoot: "/tmp/runs",
      dbPath: "/tmp/runs/db.sqlite3",
      defaultGenerations: 4,
      piNoContextFiles: true,
    });
    const settings = parseAppSettings(input);
    expect(settings.agentProvider).toBe("deterministic");
    expect(settings.piNoContextFiles).toBe(true);
  });
});
