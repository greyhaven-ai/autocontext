import { existsSync, mkdtempSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  createLocalRuntimeCommandGrant,
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

  it("rejects in-memory file and directory path collisions", async () => {
    const env = createInMemoryWorkspaceEnv({ cwd: "/project" });
    await env.writeFile("node", "file\n");

    await expect(env.writeFile("node/child.txt", "child\n")).rejects.toThrow(
      "Not a directory: /project/node",
    );
    await expect(env.mkdir("node/child", { recursive: true })).rejects.toThrow(
      "Not a directory: /project/node",
    );

    const other = createInMemoryWorkspaceEnv({ cwd: "/project" });
    await other.mkdir("node", { recursive: true });

    await expect(other.writeFile("node", "file\n")).rejects.toThrow(
      "Is a directory: /project/node",
    );
    await expect(env.mkdir("node")).rejects.toThrow("File exists: /project/node");
  });

  it("rejects in-memory file collisions during fixture setup", () => {
    expect(() =>
      createInMemoryWorkspaceEnv({
        files: {
          node: "file\n",
          "node/child.txt": "child\n",
        },
      }),
    ).toThrow("Not a directory: /node");
  });

  it("maps local workspace file operations through the virtual root", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-workspace-"));
    const env = createLocalWorkspaceEnv({ root, cwd: "/repo" });

    await env.writeFile("src/index.ts", "console.log('hello');\n");

    expect(env.resolvePath("src/index.ts")).toBe("/repo/src/index.ts");
    expect(await env.readFile("/repo/src/index.ts")).toBe("console.log('hello');\n");
    expect(await env.readdir("src")).toEqual(["index.ts"]);
  });

  it("stats and removes a local symlink without deleting the target", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-workspace-"));
    const env = createLocalWorkspaceEnv({ root, cwd: "/repo" });
    await env.mkdir("target", { recursive: true });
    await env.writeFile("target/keep.txt", "safe\n");
    symlinkSync(join(root, "repo", "target"), join(root, "repo", "link"), "dir");

    const linkStat = await env.stat("link");
    expect(linkStat.isSymbolicLink).toBe(true);
    expect(linkStat.isDirectory).toBe(false);

    await env.rm("link", { recursive: true });

    expect(await env.exists("link")).toBe(false);
    expect(await env.readFile("target/keep.txt")).toBe("safe\n");
  });

  it("keeps lexical escape paths inside the local workspace root", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-workspace-"));
    const env = createLocalWorkspaceEnv({ root, cwd: "/repo" });
    await env.writeFile("../../inside-root.txt", "inside\n");

    expect(await env.readFile("/inside-root.txt")).toBe("inside\n");
    expect(existsSync(join(root, "inside-root.txt"))).toBe(true);
    expect(existsSync(join(dirname(root), "inside-root.txt"))).toBe(false);
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

  it("runs local command grants without shell expansion and redacts trusted env from events", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-workspace-"));
    const observed: unknown[] = [];
    const env = createLocalWorkspaceEnv({ root, cwd: "/repo" });
    await env.mkdir(".", { recursive: true });
    const scoped = await env.scope({
      grantEventSink: {
        onRuntimeGrantEvent: (event) => {
          observed.push(event);
        },
      },
      commands: [
        createLocalRuntimeCommandGrant("node-secret", process.execPath, {
          args: ["-e", "process.stdout.write(process.env.AUTOCTX_TOKEN ?? '')"],
          env: { AUTOCTX_TOKEN: "trusted-secret" },
        }),
      ],
    });

    const result = await scoped.exec("node-secret");

    expect(result).toEqual({
      stdout: "trusted-secret",
      stderr: "",
      exitCode: 0,
    });
    expect(JSON.stringify(observed)).not.toContain("trusted-secret");
    expect(observed).toMatchObject([
      {
        kind: "command",
        phase: "start",
        name: "node-secret",
        cwd: "/repo",
        redaction: { envKeys: ["AUTOCTX_TOKEN"] },
      },
      {
        kind: "command",
        phase: "end",
        name: "node-secret",
        cwd: "/repo",
        exitCode: 0,
        stdout: "[redacted]",
        redaction: {
          envKeys: ["AUTOCTX_TOKEN"],
          stdout: { redacted: true, truncated: false },
        },
      },
    ]);
  });

  it("does not pass unallowlisted host env into local command grants", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-workspace-"));
    const previous = process.env.AUTOCTX_HOST_SECRET;
    process.env.AUTOCTX_HOST_SECRET = "host-secret";
    try {
      const observed: unknown[] = [];
      const env = createLocalWorkspaceEnv({ root, cwd: "/repo" });
      await env.mkdir(".", { recursive: true });
      const scoped = await env.scope({
        grantEventSink: {
          onRuntimeGrantEvent: (event) => {
            observed.push(event);
          },
        },
        commands: [
          createLocalRuntimeCommandGrant("node-host-env", process.execPath, {
            args: ["-e", "process.stdout.write(process.env.AUTOCTX_HOST_SECRET ?? '')"],
          }),
        ],
      });

      const result = await scoped.exec("node-host-env");

      expect(result).toEqual({ stdout: "", stderr: "", exitCode: 0 });
      expect(JSON.stringify(observed)).not.toContain("host-secret");
    } finally {
      if (previous === undefined) {
        delete process.env.AUTOCTX_HOST_SECRET;
      } else {
        process.env.AUTOCTX_HOST_SECRET = previous;
      }
    }
  });

  it("redacts exec env values supplied to scoped command grants", async () => {
    const observed: unknown[] = [];
    const env = createInMemoryWorkspaceEnv({ cwd: "/project" });
    const scoped = await env.scope({
      grantEventSink: {
        onRuntimeGrantEvent: (event) => {
          observed.push(event);
        },
      },
      commands: [
        defineRuntimeCommand("echo-env", async (_args, context) => ({
          stdout: context.env.AUTOCTX_EXEC_SECRET ?? "",
          stderr: "",
          exitCode: 0,
        })),
      ],
    });

    const result = await scoped.exec("echo-env", {
      env: { AUTOCTX_EXEC_SECRET: "exec-secret" },
    });

    expect(result.stdout).toBe("exec-secret");
    expect(JSON.stringify(observed)).not.toContain("exec-secret");
    expect(observed).toMatchObject([
      {
        phase: "start",
        redaction: { envKeys: ["AUTOCTX_EXEC_SECRET"] },
      },
      {
        phase: "end",
        stdout: "[redacted]",
        redaction: {
          envKeys: ["AUTOCTX_EXEC_SECRET"],
          stdout: { redacted: true },
        },
      },
    ]);
  });

  it("applies call-site exec timeouts to local command grants", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-workspace-"));
    const env = createLocalWorkspaceEnv({ root, cwd: "/repo" });
    await env.mkdir(".", { recursive: true });
    const scoped = await env.scope({
      commands: [
        createLocalRuntimeCommandGrant("node-hang", process.execPath, {
          args: ["-e", "setTimeout(() => {}, 1000)"],
        }),
      ],
    });

    const result = await scoped.exec("node-hang", { timeoutMs: 25 });

    expect(result.exitCode).toBe(124);
    expect(result.stderr).toBe("Command timed out");
  });
});
