import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { isAbsolute, join, resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  buildNodeAgentAppTarget,
  createNodeAgentAppServer,
  loadNodeAgentAppRuntimeFactory,
  planNodeAgentAppBuildTarget,
} from "../src/control-plane/agent-app-node/index.js";
import {
  RuntimeSessionEventLog,
  type RuntimeSessionEventStore,
} from "../src/session/runtime-events.js";

async function listen(server: Awaited<ReturnType<typeof createNodeAgentAppServer>>): Promise<string> {
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve());
  });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("missing server address");
  return `http://127.0.0.1:${address.port}`;
}

async function close(server: Awaited<ReturnType<typeof createNodeAgentAppServer>>): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
  });
}

function resolveFileDependency(packageDir: string, dependency: string): string {
  if (!dependency.startsWith("file:")) throw new Error(`expected file dependency: ${dependency}`);
  const target = dependency.slice("file:".length);
  return isAbsolute(target) ? target : resolve(packageDir, target);
}

describe("Node agent app build target", () => {
  let root: string;

  beforeEach(() => {
    root = mkdtempSync(join(tmpdir(), "autoctx-node-agent-build-"));
    mkdirSync(join(root, ".autoctx", "agents"), { recursive: true });
  });

  afterEach(() => {
    rmSync(root, { recursive: true, force: true });
  });

  it("plans a control-plane Node build without reading host env or changing the handler API", () => {
    const plan = planNodeAgentAppBuildTarget({ cwd: root });

    expect(plan.target).toBe("node");
    expect(plan.outputDir).toBe(join(root, ".autoctx", "build", "node"));
    expect(plan.handlerDir).toBe(".autoctx/agents");
    expect(plan.routes).toEqual(["GET /manifest", "POST /agents/:agent/invoke"]);
    expect(plan.files.map((file) => file.relativePath).sort()).toEqual([
      ".gitignore",
      "README.md",
      "package.json",
      "server.mjs",
    ]);
    expect(plan.files.find((file) => file.relativePath === "server.mjs")?.content).toContain(
      "autoctx/control-plane/agent-app-node",
    );
    expect(plan.files.find((file) => file.relativePath === "server.mjs")?.content).toContain(
      "autoctx/agent-runtime",
    );
    expect(plan.files.find((file) => file.relativePath === "server.mjs")?.content).not.toContain(
      process.env.PATH ?? "__PATH_NOT_SET__",
    );
  });

  it("materializes a minimal self-hosted Node package", async () => {
    const result = await buildNodeAgentAppTarget({ cwd: root, outDir: "dist-agent" });

    expect(result.target).toBe("node");
    expect(result.outputDir).toBe(join(root, "dist-agent"));
    expect(existsSync(join(root, "dist-agent", "server.mjs"))).toBe(true);
    const packageDir = join(root, "dist-agent");
    const packageJson = JSON.parse(readFileSync(join(packageDir, "package.json"), "utf-8")) as {
      type: string;
      scripts: { start: string };
      dependencies: { autoctx: string };
    };
    expect(packageJson).toMatchObject({
      type: "module",
      scripts: { start: "node server.mjs" },
    });
    expect(packageJson.dependencies.autoctx).toMatch(/^file:/);
    expect(
      JSON.parse(
        readFileSync(join(resolveFileDependency(packageDir, packageJson.dependencies.autoctx), "package.json"), "utf-8"),
      ),
    ).toMatchObject({
      name: "autoctx",
      exports: {
        "./control-plane/agent-app-node": expect.any(Object),
      },
    });
    expect(readFileSync(join(root, "dist-agent", "server.mjs"), "utf-8")).toContain(
      'projectRoot: new URL("..", import.meta.url)',
    );
    expect(readFileSync(join(root, "dist-agent", "README.md"), "utf-8")).toContain(
      "docs/core-control-package-split.md#agent-app-build-targets",
    );
  });

  it("loads runtime factories from bare packages installed in the generated app root", async () => {
    mkdirSync(join(root, "node_modules", "runtime-factory"), { recursive: true });
    writeFileSync(
      join(root, "node_modules", "runtime-factory", "package.json"),
      JSON.stringify({ name: "runtime-factory", type: "module", main: "index.mjs" }),
    );
    writeFileSync(
      join(root, "node_modules", "runtime-factory", "index.mjs"),
      [
        "export default function createRuntime(plan) {",
        "  return {",
        "    name: `bare-runtime:${plan.agentName}:${plan.id}`,",
        "    async generate() { return { text: plan.env.RUNTIME_TOKEN ?? 'missing' }; },",
        "    async revise() { return { text: 'unused' }; }",
        "  };",
        "}",
      ].join("\n"),
    );

    const factory = await loadNodeAgentAppRuntimeFactory("runtime-factory", root);
    const runtime = await factory({
      agentName: "support",
      id: "ticket-123",
      env: { RUNTIME_TOKEN: "token-from-env" },
    });
    if (!runtime || "runtime" in runtime) {
      throw new Error("expected direct AgentRuntime handle");
    }

    expect(runtime.name).toBe("bare-runtime:support:ticket-123");
    await expect(runtime.generate({ prompt: "hello" })).resolves.toEqual({ text: "token-from-env" });
  });

  it("serves pure local handlers through the generated Node server shape", async () => {
    writeFileSync(
      join(root, ".autoctx", "agents", "support.mjs"),
      [
        "export const triggers = { webhook: true };",
        "export default async function (ctx) {",
        "  return { id: ctx.id, message: ctx.payload.message, token: ctx.env.SUPPORT_TOKEN, leaked: ctx.env.SECRET_TOKEN };",
        "}",
      ].join("\n"),
    );
    writeFileSync(join(root, ".env.local"), "SUPPORT_TOKEN=file-token\n");
    let createRuntimeCalls = 0;

    const server = await createNodeAgentAppServer({
      projectRoot: root,
      envFile: ".env.local",
      processEnv: {
        SUPPORT_TOKEN: "shell-token",
        SECRET_TOKEN: "must-not-be-captured",
      },
      createRuntime: () => {
        createRuntimeCalls += 1;
        throw new Error("runtime should be lazy for local handlers");
      },
    });
    const baseUrl = await listen(server);
    try {
      const manifest = await fetch(`${baseUrl}/manifest`);
      expect(manifest.status).toBe(200);
      expect(await manifest.json()).toMatchObject({
        ok: true,
        agents: [{ name: "support", relativePath: ".autoctx/agents/support.mjs", triggers: { webhook: true } }],
      });

      const invocation = await fetch(`${baseUrl}/agents/support/invoke`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ id: "ticket-123", payload: { message: "please triage" } }),
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
      expect(createRuntimeCalls).toBe(0);
    } finally {
      await close(server);
    }
  });

  it("invokes runtime-backed handlers and records runtime-session events through the store contract", async () => {
    writeFileSync(
      join(root, ".autoctx", "agents", "prompted.mjs"),
      [
        "export default async function (ctx) {",
        "  const runtime = await ctx.init();",
        "  const session = await runtime.session('default');",
        "  const response = await session.prompt(ctx.payload.prompt);",
        "  return { text: response.text, sessionId: response.sessionId };",
        "}",
      ].join("\n"),
    );
    const savedLogs = new Map<string, RuntimeSessionEventLog>();
    const store = {
      save: (log: RuntimeSessionEventLog) => {
        savedLogs.set(log.sessionId, RuntimeSessionEventLog.fromJSON(log.toJSON()));
      },
      load: (sessionId: string) => savedLogs.get(sessionId) ?? null,
      list: () => [...savedLogs.values()],
      listChildren: () => [],
      close: () => {},
    } as unknown as RuntimeSessionEventStore;

    const server = await createNodeAgentAppServer({
      projectRoot: root,
      eventStore: store,
      createRuntime: () => ({
        name: "fake-node-target-runtime",
        generate: async ({ prompt }) => ({ text: `echo:${prompt}` }),
        revise: async () => ({ text: "unused" }),
      }),
    });
    const baseUrl = await listen(server);
    try {
      const invocation = await fetch(`${baseUrl}/agents/prompted/invoke`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ id: "run-1", payload: { prompt: "hello" } }),
      });
      expect(invocation.status).toBe(200);
      expect(await invocation.json()).toMatchObject({
        ok: true,
        agent: "prompted",
        id: "run-1",
        result: { text: "echo:hello", sessionId: "agent:prompted:default" },
      });

      const log = savedLogs.get("agent:prompted:default");
      expect(log?.metadata).toMatchObject({
        agentName: "prompted",
        experimentalAgentRuntime: true,
      });
      expect(log?.events.map((event) => event.eventType)).toEqual([
        "prompt_submitted",
        "assistant_message",
      ]);
    } finally {
      await close(server);
    }
  });
});
