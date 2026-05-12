import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  AGENT_COMMAND_HELP_TEXT,
  createAutoctxAgentDevServer,
  executeAutoctxAgentRunCommandWorkflow,
  loadAutoctxAgentEnvFile,
  planAutoctxAgentCommand,
  renderAutoctxAgentCommandError,
} from "../src/cli/agent-command-workflow.js";

describe("agent command workflow", () => {
  let root: string;

  beforeEach(() => {
    root = mkdtempSync(join(tmpdir(), "autoctx-agent-command-"));
    mkdirSync(join(root, ".autoctx", "agents"), { recursive: true });
  });

  afterEach(() => {
    rmSync(root, { recursive: true, force: true });
  });

  it("exposes stable help text for run and dev", () => {
    expect(AGENT_COMMAND_HELP_TEXT).toContain("autoctx agent run <agent>");
    expect(AGENT_COMMAND_HELP_TEXT).toContain("autoctx agent dev");
    expect(AGENT_COMMAND_HELP_TEXT).toContain("--payload");
    expect(AGENT_COMMAND_HELP_TEXT).toContain("--env");
    expect(AGENT_COMMAND_HELP_TEXT).toContain("--json");
  });

  it("plans one-shot agent runs with invocation id and JSON payload", () => {
    expect(
      planAutoctxAgentCommand(
        {
          id: "ticket-123",
          payload: "{\"message\":\"please triage\"}",
          env: ".env.local",
          json: true,
        },
        ["run", "support"],
      ),
    ).toEqual({
      action: "run",
      agentName: "support",
      id: "ticket-123",
      payload: { message: "please triage" },
      envPath: ".env.local",
      cwd: undefined,
      json: true,
      provider: undefined,
      model: undefined,
      apiKey: undefined,
      baseUrl: undefined,
    });
  });

  it("plans dev server startup", () => {
    expect(
      planAutoctxAgentCommand(
        {
          port: "3584",
          env: ".env.local",
          json: true,
        },
        ["dev"],
      ),
    ).toEqual({
      action: "dev",
      port: 3584,
      host: "127.0.0.1",
      envPath: ".env.local",
      cwd: undefined,
      json: true,
      provider: undefined,
      model: undefined,
      apiKey: undefined,
      baseUrl: undefined,
    });
  });

  it("rejects malformed JSON payloads with an actionable message", () => {
    expect(() =>
      planAutoctxAgentCommand({ payload: "{bad" }, ["run", "support"]),
    ).toThrow("--payload must be valid JSON");
  });

  it("loads explicit env files without overriding shell-set values", () => {
    writeFileSync(
      join(root, ".env.local"),
      [
        "# comment",
        "SUPPORT_TOKEN=file-token",
        "QUOTED=\"file value\"",
        "export EMPTY=",
      ].join("\n"),
    );

    expect(
      loadAutoctxAgentEnvFile(join(root, ".env.local"), {
        SUPPORT_TOKEN: "shell-token",
      }),
    ).toEqual({
      SUPPORT_TOKEN: "shell-token",
      QUOTED: "file value",
      EMPTY: "",
    });
  });

  it("invokes a named handler with id, payload, env, and CI-safe JSON output", async () => {
    writeFileSync(
      join(root, ".autoctx", "agents", "support.mjs"),
      [
        "export const triggers = { webhook: true };",
        "export default async function (ctx) {",
        "  return { id: ctx.id, agent: ctx.agent.name, payload: ctx.payload, token: ctx.env.SUPPORT_TOKEN };",
        "}",
      ].join("\n"),
    );
    writeFileSync(join(root, ".env.local"), "SUPPORT_TOKEN=file-token\n");

    const result = await executeAutoctxAgentRunCommandWorkflow({
      cwd: root,
      processEnv: { SUPPORT_TOKEN: "shell-token" },
      plan: {
        action: "run",
        agentName: "support",
        id: "ticket-123",
        payload: { message: "please triage" },
        envPath: ".env.local",
        json: true,
      },
    });

    expect(JSON.parse(result.stdout)).toEqual({
      ok: true,
      agent: "support",
      id: "ticket-123",
      result: {
        id: "ticket-123",
        agent: "support",
        payload: { message: "please triage" },
        token: "shell-token",
      },
    });
    expect(result.exitCode).toBe(0);
    expect(result.stderr).toBe("");
  });

  it("does not create a provider runtime for pure local handlers", async () => {
    writeFileSync(
      join(root, ".autoctx", "agents", "local.mjs"),
      [
        "export default async function (ctx) {",
        "  return { id: ctx.id, message: ctx.payload.message, localOnly: true };",
        "}",
      ].join("\n"),
    );
    let createRuntimeCalls = 0;

    const result = await executeAutoctxAgentRunCommandWorkflow({
      cwd: root,
      processEnv: {},
      createRuntime: () => {
        createRuntimeCalls += 1;
        throw new Error("ANTHROPIC_API_KEY environment variable required");
      },
      plan: {
        action: "run",
        agentName: "local",
        id: "local-1",
        payload: { message: "offline" },
        json: true,
      },
    });

    expect(createRuntimeCalls).toBe(0);
    expect(JSON.parse(result.stdout)).toMatchObject({
      ok: true,
      agent: "local",
      id: "local-1",
      result: {
        id: "local-1",
        message: "offline",
        localOnly: true,
      },
    });
  });

  it("loads env files before creating a runtime for prompt-backed handlers", async () => {
    writeFileSync(
      join(root, ".autoctx", "agents", "prompted.mjs"),
      [
        "export default async function (ctx) {",
        "  const runtime = await ctx.init();",
        "  const session = await runtime.session('default');",
        "  const response = await session.prompt(ctx.payload.prompt);",
        "  return { text: response.text, token: ctx.env.ANTHROPIC_API_KEY };",
        "}",
      ].join("\n"),
    );
    writeFileSync(join(root, ".env.local"), "ANTHROPIC_API_KEY=file-key\n");
    let runtimeEnv: Record<string, string> | undefined;

    const result = await executeAutoctxAgentRunCommandWorkflow({
      cwd: root,
      processEnv: {},
      createRuntime: (runtimePlan) => {
        runtimeEnv = { ...runtimePlan.env };
        return {
          name: "fake-provider",
          generate: async () => ({
            text: runtimePlan.env.ANTHROPIC_API_KEY ?? "missing",
          }),
          revise: async () => ({ text: "unused" }),
        };
      },
      plan: {
        action: "run",
        agentName: "prompted",
        id: "prompt-1",
        payload: { prompt: "hello" },
        envPath: ".env.local",
        json: true,
      },
    });

    expect(runtimeEnv).toEqual({ ANTHROPIC_API_KEY: "file-key" });
    expect(JSON.parse(result.stdout)).toMatchObject({
      ok: true,
      result: {
        text: "file-key",
        token: "file-key",
      },
    });
  });

  it("renders structured JSON errors", () => {
    expect(
      JSON.parse(renderAutoctxAgentCommandError(new Error("agent missing"), true)),
    ).toEqual({
      ok: false,
      error: {
        code: "AUTOCTX_AGENT_ERROR",
        message: "agent missing",
      },
    });
  });

  it("serves a manifest and invocation endpoint from the same runner path", async () => {
    writeFileSync(
      join(root, ".autoctx", "agents", "support.mjs"),
      [
        "export const triggers = { webhook: true };",
        "export default async function (ctx) {",
        "  return { id: ctx.id, message: ctx.payload.message, token: ctx.env.SUPPORT_TOKEN };",
        "}",
      ].join("\n"),
    );
    writeFileSync(join(root, ".env.local"), "SUPPORT_TOKEN=file-token\n");

    const server = await createAutoctxAgentDevServer({
      cwd: root,
      envPath: ".env.local",
      processEnv: { SUPPORT_TOKEN: "shell-token" },
    });
    await new Promise<void>((resolve, reject) => {
      server.once("error", reject);
      server.listen(0, "127.0.0.1", () => resolve());
    });
    try {
      const address = server.address();
      if (!address || typeof address === "string") throw new Error("missing server address");
      const baseUrl = `http://127.0.0.1:${address.port}`;

      const manifest = await fetch(`${baseUrl}/manifest`);
      expect(manifest.status).toBe(200);
      expect(await manifest.json()).toMatchObject({
        ok: true,
        agents: [
          {
            name: "support",
            relativePath: ".autoctx/agents/support.mjs",
            triggers: { webhook: true },
          },
        ],
      });

      const invocation = await fetch(`${baseUrl}/agents/support/invoke`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          id: "ticket-123",
          payload: { message: "please triage" },
        }),
      });
      expect(invocation.status).toBe(200);
      expect(await invocation.json()).toEqual({
        ok: true,
        agent: "support",
        id: "ticket-123",
        result: {
          id: "ticket-123",
          message: "please triage",
          token: "shell-token",
        },
      });
    } finally {
      await new Promise<void>((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      });
    }
  });
});
