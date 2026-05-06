import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  createInMemoryWorkspaceEnv,
  createLocalWorkspaceEnv,
  defineRuntimeCommand,
} from "../src/runtimes/workspace-env.js";

describe("RuntimeWorkspaceEnv", () => {
  it("normalizes virtual paths and supports in-memory file operations", async () => {
    const env = createInMemoryWorkspaceEnv({ cwd: "/project" });

    await env.writeFile("src/app.ts", "export const answer = 42;\n");

    expect(env.resolvePath("src/app.ts")).toBe("/project/src/app.ts");
    expect(await env.readFile("/project/src/app.ts")).toBe("export const answer = 42;\n");
    expect(await env.exists("src/app.ts")).toBe(true);
    expect(await env.exists("src/missing.ts")).toBe(false);
    expect(await env.readdir("src")).toEqual(["app.ts"]);

    const fileStat = await env.stat("src/app.ts");
    expect(fileStat.isFile).toBe(true);
    expect(fileStat.isDirectory).toBe(false);
    expect(fileStat.size).toBe(Buffer.byteLength("export const answer = 42;\n"));

    const dirStat = await env.stat("src");
    expect(dirStat.isDirectory).toBe(true);
  });

  it("scopes in-memory environments without copying the filesystem", async () => {
    const env = createInMemoryWorkspaceEnv({ cwd: "/project" });
    await env.writeFile("README.md", "root\n");

    const scoped = await env.scope({ cwd: "packages/core" });
    await scoped.writeFile("README.md", "core\n");

    expect(scoped.cwd).toBe("/project/packages/core");
    expect(await scoped.readFile("README.md")).toBe("core\n");
    expect(await env.readFile("README.md")).toBe("root\n");
    expect(await env.readFile("packages/core/README.md")).toBe("core\n");
  });

  it("maps local workspace file operations through the virtual root", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-workspace-"));
    const env = createLocalWorkspaceEnv({ root, cwd: "/repo" });

    await env.writeFile("src/index.ts", "console.log('hello');\n");

    expect(env.resolvePath("src/index.ts")).toBe("/repo/src/index.ts");
    expect(await env.readFile("/repo/src/index.ts")).toBe("console.log('hello');\n");
    expect(await env.readdir("src")).toEqual(["index.ts"]);
  });

  it("executes local commands inside the requested virtual cwd", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-workspace-"));
    const env = createLocalWorkspaceEnv({ root, cwd: "/repo" });
    await env.mkdir(".", { recursive: true });

    const result = await env.exec("printf autoctx", { cwd: "/repo" });

    expect(result).toEqual({ stdout: "autoctx", stderr: "", exitCode: 0 });
  });

  it("scopes command grants to a child environment", async () => {
    const env = createInMemoryWorkspaceEnv({ cwd: "/project" });
    const scoped = await env.scope({
      commands: [
        defineRuntimeCommand("greet", async (args) => ({
          stdout: `hello ${args.join(" ")}`,
          stderr: "",
          exitCode: 0,
        })),
      ],
    });

    expect(await scoped.exec("greet Ada Lovelace")).toEqual({
      stdout: "hello Ada Lovelace",
      stderr: "",
      exitCode: 0,
    });
    expect((await env.exec("greet Ada")).exitCode).toBe(127);
  });

  it("passes trusted command env and virtual cwd to grants", async () => {
    const env = createInMemoryWorkspaceEnv({ cwd: "/project" });
    const scoped = await env.scope({
      cwd: "packages/core",
      commands: [
        defineRuntimeCommand(
          "show-context",
          async (_args, context) => ({
            stdout: `${context.cwd}:${context.env.AUTOCTX_TOKEN ?? ""}`,
            stderr: "",
            exitCode: 0,
          }),
          { env: { AUTOCTX_TOKEN: "trusted-secret" } },
        ),
      ],
    });

    const result = await scoped.exec("show-context", {
      env: { AUTOCTX_TOKEN: "prompt-value" },
    });

    expect(result.stdout).toBe("/project/packages/core:trusted-secret");
  });

  it("lets scoped local command grants coexist with shell fallback", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-workspace-"));
    const env = createLocalWorkspaceEnv({ root, cwd: "/repo" });
    await env.mkdir(".", { recursive: true });
    const scoped = await env.scope({
      commands: [
        defineRuntimeCommand("agent-tool", async () => ({
          stdout: "from grant",
          stderr: "",
          exitCode: 0,
        })),
      ],
    });

    expect(await scoped.exec("agent-tool")).toEqual({
      stdout: "from grant",
      stderr: "",
      exitCode: 0,
    });
    expect(await scoped.exec("printf shell")).toEqual({
      stdout: "shell",
      stderr: "",
      exitCode: 0,
    });
  });
});
