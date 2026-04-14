import { describe, expect, it } from "vitest";

import { buildCapabilitiesPayload } from "../src/cli/capabilities-command-workflow.js";

describe("capabilities command workflow", () => {
  it("builds capabilities payload with CLI command inventory and feature flags", () => {
    expect(
      buildCapabilitiesPayload({
        version: "0.3.7",
        scenarios: ["grid_ctf"],
        providers: ["deterministic"],
        features: ["generation_loop"],
        pythonOnly: ["train"],
        concept_model: {
          source_doc: "docs/concept-model.md",
          user_facing: [],
          runtime: [],
        },
      }, null),
    ).toMatchObject({
      version: "0.3.7",
      scenarios: ["grid_ctf"],
      providers: ["deterministic"],
      commands: expect.arrayContaining([
        "init",
        "run",
        "capabilities",
        "login",
        "whoami",
        "logout",
        "providers",
        "models",
        "mission",
        "campaign",
        "tui",
        "judge",
        "improve",
        "repl",
        "queue",
        "status",
        "serve",
        "mcp-serve",
        "version",
      ]),
      features: {
        mcp_server: true,
        training_export: true,
        custom_scenarios: true,
        interactive_server: true,
        playbook_versioning: true,
      },
      project_config: null,
    });
  });

  it("preserves project config when provided", () => {
    const projectConfig = {
      default_scenario: "grid_ctf",
      provider: "deterministic",
      model: "fixture-model",
      active_runs: 1,
      total_runs: 2,
      knowledge_state: { exists: true, directories: 1, files: 2 },
    };

    expect(
      buildCapabilitiesPayload(
        {
          version: "0.3.7",
          scenarios: ["grid_ctf"],
          providers: ["deterministic"],
          features: ["generation_loop"],
          pythonOnly: ["train"],
          concept_model: {
            source_doc: "docs/concept-model.md",
            user_facing: [],
            runtime: [],
          },
        },
        projectConfig,
      ).project_config,
    ).toEqual(projectConfig);
  });
});
