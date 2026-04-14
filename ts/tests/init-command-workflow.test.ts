import { describe, expect, it } from "vitest";

import {
  buildInitSuccessMessages,
  INIT_HELP_TEXT,
  planInitCommand,
} from "../src/cli/init-command-workflow.js";

describe("init command workflow", () => {
  it("exposes stable help text", () => {
    expect(INIT_HELP_TEXT).toContain("autoctx init");
    expect(INIT_HELP_TEXT).toContain("--dir");
    expect(INIT_HELP_TEXT).toContain("--scenario");
    expect(INIT_HELP_TEXT).toContain("--provider");
    expect(INIT_HELP_TEXT.toLowerCase()).toContain("see also");
  });

  it("rejects existing config targets", () => {
    expect(() =>
      planInitCommand(
        {
          dir: "/tmp/project",
          scenario: undefined,
          provider: undefined,
          model: undefined,
          gens: undefined,
          "agents-md": false,
        },
        {
          resolvePath: (value: string) => value,
          joinPath: (...parts: string[]) => parts.join("/"),
          configExists: true,
          projectDefaults: null,
          persistedCredentials: null,
          env: {},
          resolveProviderConfig: () => ({ providerType: "anthropic", model: "claude" }),
          parsePositiveInteger: (_value: string | undefined, _label: string) => 3,
        },
      ),
    ).toThrow("Error: .autoctx.json already exists in /tmp/project");
  });

  it("plans sensible defaults when no provider config resolves", () => {
    expect(
      planInitCommand(
        {
          dir: "/tmp/project",
          scenario: undefined,
          provider: undefined,
          model: undefined,
          gens: undefined,
          "agents-md": false,
        },
        {
          resolvePath: (value: string) => value,
          joinPath: (...parts: string[]) => parts.join("/"),
          configExists: false,
          projectDefaults: null,
          persistedCredentials: null,
          env: {},
          resolveProviderConfig: () => {
            throw new Error("not configured");
          },
          parsePositiveInteger: (_value: string | undefined, _label: string) => 3,
        },
      ),
    ).toEqual({
      targetDir: "/tmp/project",
      configPath: "/tmp/project/.autoctx.json",
      config: {
        default_scenario: "grid_ctf",
        provider: "deterministic",
        gens: 3,
        knowledge_dir: "./knowledge",
        runs_dir: "./runs",
      },
    });
  });

  it("uses init/provider/model precedence before fallback resolution", () => {
    expect(
      planInitCommand(
        {
          dir: "/tmp/project",
          scenario: "workflow",
          provider: "ollama",
          model: "llama3.2",
          gens: "5",
          "agents-md": true,
        },
        {
          resolvePath: (value: string) => value,
          joinPath: (...parts: string[]) => parts.join("/"),
          configExists: false,
          projectDefaults: {
            defaultScenario: "grid_ctf",
            provider: "anthropic",
            model: "claude",
          },
          persistedCredentials: {
            provider: "openai",
            model: "gpt-4o",
          },
          env: {
            AUTOCONTEXT_AGENT_PROVIDER: "gemini",
            AUTOCONTEXT_AGENT_DEFAULT_MODEL: "gemini-2.5-pro",
          },
          resolveProviderConfig: () => ({ providerType: "deterministic", model: "fixture-model" }),
          parsePositiveInteger: (value: string | undefined, _label: string) => Number(value),
        },
      ),
    ).toEqual({
      targetDir: "/tmp/project",
      configPath: "/tmp/project/.autoctx.json",
      config: {
        default_scenario: "workflow",
        provider: "ollama",
        model: "llama3.2",
        gens: 5,
        knowledge_dir: "./knowledge",
        runs_dir: "./runs",
      },
    });
  });

  it("uses resolved provider config when explicit/project/env credentials are absent", () => {
    expect(
      planInitCommand(
        {
          dir: "/tmp/project",
          scenario: undefined,
          provider: undefined,
          model: undefined,
          gens: "4",
          "agents-md": false,
        },
        {
          resolvePath: (value: string) => value,
          joinPath: (...parts: string[]) => parts.join("/"),
          configExists: false,
          projectDefaults: null,
          persistedCredentials: null,
          env: {},
          resolveProviderConfig: () => ({ providerType: "anthropic", model: "claude-sonnet" }),
          parsePositiveInteger: (value: string | undefined, _label: string) => Number(value),
        },
      ).config,
    ).toEqual({
      default_scenario: "grid_ctf",
      provider: "anthropic",
      model: "claude-sonnet",
      gens: 4,
      knowledge_dir: "./knowledge",
      runs_dir: "./runs",
    });
  });

  it("renders init success messages", () => {
    expect(
      buildInitSuccessMessages({
        configPath: "/tmp/project/.autoctx.json",
        agentsPath: "/tmp/project/AGENTS.md",
        agentsMdUpdated: true,
      }),
    ).toEqual([
      "Created /tmp/project/.autoctx.json",
      "Updated /tmp/project/AGENTS.md",
    ]);

    expect(
      buildInitSuccessMessages({
        configPath: "/tmp/project/.autoctx.json",
        agentsPath: "/tmp/project/AGENTS.md",
        agentsMdUpdated: false,
      }),
    ).toEqual([
      "Created /tmp/project/.autoctx.json",
      "AGENTS.md already contained AutoContext guidance",
    ]);
  });
});
