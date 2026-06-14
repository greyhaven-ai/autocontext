import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import type { AutoctxAgentContext } from "../src/agent-runtime/index.js";
import {
  createAgentAppFetchHandler,
  createAgentAppFetchWorkspaceEnv,
  createEdgeInMemoryWorkspaceEnv,
  createInMemoryAgentAppFetchWorkspaceStore,
  createStaticAgentAppCatalog,
} from "../src/control-plane/agent-app-fetch/index.js";

async function jsonBody(response: Response): Promise<unknown> {
  return await response.json();
}

function request(path: string, init?: RequestInit): Request {
  return new Request(`https://agent-app.test${path}`, init);
}

describe("agent app Fetch workspace store contract", () => {
  it("runs handlers against an explicit host-created workspace store", async () => {
    const workspaceStore = createInMemoryAgentAppFetchWorkspaceStore();
    expect(workspaceStore.capabilities).toEqual({
      persistence: "request_memory",
      consistency: "read_your_writes_after_write",
      listing: "lexicographic",
      unsupportedOperations: ["exec"],
    });
    const handler = createAgentAppFetchHandler({
      workspaceStore,
      catalog: createStaticAgentAppCatalog([
        {
          name: "writer",
          relativePath: ".autoctx/agents/writer.mjs",
          extension: ".mjs",
          handler: async (ctx: AutoctxAgentContext<{ message: string }>) => {
            const previousExists = await ctx.workspace.exists("artifacts/latest.txt");
            const previous = previousExists
              ? await ctx.workspace.readFile("artifacts/latest.txt")
              : null;
            await ctx.workspace.writeFile("artifacts/latest.txt", ctx.payload.message);
            const stat = await ctx.workspace.stat("artifacts/latest.txt");
            return {
              id: ctx.id,
              previous,
              current: await ctx.workspace.readFile("artifacts/latest.txt"),
              entries: await ctx.workspace.readdir("artifacts"),
              stat: {
                isFile: stat.isFile,
                isDirectory: stat.isDirectory,
                size: stat.size,
              },
            };
          },
        },
      ]),
    });

    const first = await handler(
      request("/agents/writer/invoke", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ id: "run-1", payload: { message: "first" } }),
      }),
    );
    const second = await handler(
      request("/agents/writer/invoke", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ id: "run-2", payload: { message: "second" } }),
      }),
    );

    expect(first.status).toBe(200);
    expect(await jsonBody(first)).toMatchObject({
      ok: true,
      id: "run-1",
      result: {
        previous: null,
        current: "first",
        entries: ["latest.txt"],
        stat: { isFile: true, isDirectory: false, size: 5 },
      },
    });
    expect(second.status).toBe(200);
    expect(await jsonBody(second)).toMatchObject({
      ok: true,
      id: "run-2",
      result: {
        previous: "first",
        current: "second",
        entries: ["latest.txt"],
        stat: { isFile: true, isDirectory: false, size: 6 },
      },
    });
  });

  it("preserves file and directory semantics for an in-memory reference store", async () => {
    const workspace = createAgentAppFetchWorkspaceEnv({
      store: createInMemoryAgentAppFetchWorkspaceStore(),
    });
    const original = new Uint8Array([1, 2, 3]);

    await workspace.writeFile("/a", "file");

    await expect(workspace.writeFile("/a/b.txt", "child")).rejects.toThrow("Not a directory: /a");
    await expect(workspace.readdir("/a")).rejects.toThrow("Directory not found: /a");
    await expect(workspace.mkdir("/a")).rejects.toThrow("File exists: /a");
    await expect(workspace.mkdir("/a/child", { recursive: true })).rejects.toThrow(
      "Not a directory: /a",
    );
    await expect(workspace.mkdir("/missing/child")).rejects.toThrow(
      "Parent directory not found: /missing",
    );
    await expect(workspace.exists("/missing")).resolves.toBe(false);

    await workspace.mkdir("/missing/child", { recursive: true });
    await workspace.writeFile("/missing/child/data.bin", original);
    original[0] = 9;

    await expect(workspace.stat("/missing/child")).resolves.toMatchObject({
      isDirectory: true,
      isFile: false,
    });
    const firstRead = await workspace.readFileBytes("/missing/child/data.bin");
    expect([...firstRead]).toEqual([1, 2, 3]);
    firstRead[1] = 8;
    await expect(workspace.readFileBytes("/missing/child/data.bin")).resolves.toEqual(
      new Uint8Array([1, 2, 3]),
    );

    await expect(workspace.rm("/missing")).rejects.toThrow("Directory not empty: /missing");
    await workspace.rm("/missing", { recursive: true });
    await expect(workspace.exists("/missing/child/data.bin")).resolves.toBe(false);
    await expect(workspace.rm("/missing", { force: true })).resolves.toBeUndefined();
  });

  it("copies Buffer-backed bytes at env and store boundaries", async () => {
    const store = createInMemoryAgentAppFetchWorkspaceStore();
    const workspace = createAgentAppFetchWorkspaceEnv({ store });
    const envBuffer = Buffer.from([1, 2, 3]);
    const storeBuffer = Buffer.from([4, 5, 6]);

    await workspace.writeFile("/env.bin", envBuffer);
    envBuffer[0] = 9;
    const envRead = await workspace.readFileBytes("/env.bin");
    expect([...envRead]).toEqual([1, 2, 3]);
    envRead[1] = 8;
    await expect(workspace.readFileBytes("/env.bin")).resolves.toEqual(
      new Uint8Array([1, 2, 3]),
    );

    await store.writeFile("/store.bin", storeBuffer);
    storeBuffer[0] = 9;
    const storeRead = await store.readFile("/store.bin");
    expect([...storeRead]).toEqual([4, 5, 6]);
    storeRead[1] = 8;
    await expect(store.readFile("/store.bin")).resolves.toEqual(new Uint8Array([4, 5, 6]));
  });

  it("clears non-root entries when recursively removing the workspace root", async () => {
    const workspace = createAgentAppFetchWorkspaceEnv({
      store: createInMemoryAgentAppFetchWorkspaceStore(),
    });

    await workspace.writeFile("/a/b.txt", "child");
    await workspace.writeFile("/root.txt", "root");

    await workspace.rm("/", { recursive: true });

    await expect(workspace.stat("/")).resolves.toMatchObject({
      isDirectory: true,
      isFile: false,
    });
    await expect(workspace.exists("/a")).resolves.toBe(false);
    await expect(workspace.exists("/a/b.txt")).resolves.toBe(false);
    await expect(workspace.exists("/root.txt")).resolves.toBe(false);
    await expect(workspace.readdir("/")).resolves.toEqual([]);
  });

  it("scopes workspace store environments without granting shell execution", async () => {
    const edgeWorkspace = createEdgeInMemoryWorkspaceEnv({ cwd: "/repo" });
    await expect(edgeWorkspace.stat(".")).resolves.toMatchObject({
      isDirectory: true,
      isFile: false,
    });

    const workspace = createAgentAppFetchWorkspaceEnv({
      store: createInMemoryAgentAppFetchWorkspaceStore(),
      cwd: "/workspace",
    });
    const scoped = await workspace.scope({ cwd: "project" });

    await scoped.writeFile("notes.md", "edge notes\n");

    expect(scoped.cwd).toBe("/workspace/project");
    await expect(workspace.readFile("project/notes.md")).resolves.toBe("edge notes\n");
    await expect(scoped.exec("echo nope")).rejects.toThrow(
      "Runtime command execution is unavailable in the generic Fetch agent app workspace",
    );
  });

  it("keeps the workspace store contract free of provider and Node storage imports", () => {
    const source = readFileSync(
      join(
        import.meta.dirname,
        "..",
        "src",
        "control-plane",
        "agent-app-fetch",
        "workspace-store.ts",
      ),
      "utf-8",
    );

    expect(source).not.toContain('"node:');
    expect(source).not.toContain("'node:");
    expect(source).not.toContain("process.env");
    expect(source).not.toMatch(/wrangler|cloudflare|durable object namespace|r2 bucket|s3/i);
  });
});
