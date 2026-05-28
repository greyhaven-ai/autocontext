import { describe, expect, it } from "vitest";

import { buildCapabilitiesPayload } from "../src/cli/capabilities-command-workflow.js";
import { visibleSupportedCommandNames } from "../src/cli/command-registry.js";

describe("capabilities command workflow", () => {
  it("builds capabilities payload with CLI command inventory and feature flags", () => {
    const payload = buildCapabilitiesPayload(
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
      null,
    );

    expect(payload).toMatchObject({
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
        "train",
        "solve",
        "simulate",
        "investigate",
        "analyze",
        "context-selection",
        "candidate",
        "eval",
        "promotion",
        "registry",
        "emit-pr",
        "production-traces",
        "instrument",
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
    expect(payload.commands).toEqual(visibleSupportedCommandNames());
    expect(payload.commands).not.toContain("ecosystem");
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

  // AC-697 slice 5: capabilities loads docs/cli-contract.json so the
  // JSON payload advertises the canonical command surface (id, path,
  // aliases, per-runtime support) sourced from the single contract.
  it("emits a `contract` field with canonical commands + aliases + per-runtime support", () => {
    const payload = buildCapabilitiesPayload(
      {
        version: "0.3.7",
        scenarios: [],
        providers: [],
        features: [],
        pythonOnly: [],
        concept_model: {
          source_doc: "docs/concept-model.md",
          user_facing: [],
          runtime: [],
        },
      },
      null,
    );
    expect(payload.contract.schema_version).toBe(1);
    expect(payload.contract.commands.length).toBeGreaterThan(0);
    // Paved-road commands from slice 1 must appear in the contract list.
    const ids = new Set(payload.contract.commands.map((c) => c.id));
    for (const required of ["solve", "run", "status", "watch", "show", "export"]) {
      expect(ids.has(required), `paved-road command ${required} missing from contract`).toBe(true);
    }
    // The sub-Typer-group entries shipped in slices 3/4 (`queue.add`,
    // `queue.status`, `scenario.create`) must also be present.
    expect(ids.has("queue.status")).toBe(true);
    expect(ids.has("scenario.create")).toBe(true);
    const scenarioCreate = payload.contract.commands.find((c) => c.id === "scenario.create");
    expect(scenarioCreate?.aliases).toContain("new-scenario");
  });

  // PR #1000 review (P2): the npm package ships
  // `docs/cli-contract.json` as `dist/cli-contract.json` via the
  // `build:cli-contract` script chained into `npm run build`. The
  // TS loader resolves from there first, falling back to the
  // repo-relative path only for the dev tree (tsx-from-source).
  it("buildCapabilitiesPayload resolves the contract from a known location", () => {
    // The loader must succeed in this test (which runs against the
    // dev tree). Both branches return an absolute path; the call
    // itself failing means the fallback walked off the file system.
    const payload = buildCapabilitiesPayload(
      {
        version: "0.3.7",
        scenarios: [],
        providers: [],
        features: [],
        pythonOnly: [],
        concept_model: {
          source_doc: "docs/concept-model.md",
          user_facing: [],
          runtime: [],
        },
      },
      null,
    );
    expect(payload.contract.schema_version).toBeGreaterThan(0);
    expect(Array.isArray(payload.contract.commands)).toBe(true);
  });

  it("contract entries carry runtime_support with python + typescript status", () => {
    const payload = buildCapabilitiesPayload(
      {
        version: "0.3.7",
        scenarios: [],
        providers: [],
        features: [],
        pythonOnly: [],
        concept_model: {
          source_doc: "docs/concept-model.md",
          user_facing: [],
          runtime: [],
        },
      },
      null,
    );
    for (const cmd of payload.contract.commands) {
      expect(cmd.runtime_support.python.status).toMatch(/^(yes|missing|intentional_gap)$/);
      expect(cmd.runtime_support.typescript.status).toMatch(/^(yes|missing|intentional_gap)$/);
    }
  });
});
