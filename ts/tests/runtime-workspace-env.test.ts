import {
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
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

  it("bootstraps a missing local root for recursive creation and writes", async () => {
    const parent = mkdtempSync(join(tmpdir(), "autoctx-workspace-parent-"));
    const mkdirRoot = join(parent, "mkdir-root");
    const writeRoot = join(parent, "write-root");

    try {
      const mkdirEnv = createLocalWorkspaceEnv({ root: mkdirRoot, cwd: "/repo" });
      await mkdirEnv.mkdir("nested", { recursive: true });
      expect(existsSync(join(mkdirRoot, "repo", "nested"))).toBe(true);

      const writeEnv = createLocalWorkspaceEnv({ root: writeRoot, cwd: "/repo" });
      await writeEnv.writeFile("nested/file.txt", "created\n");
      expect(readFileSync(join(writeRoot, "repo", "nested", "file.txt"), "utf-8")).toBe(
        "created\n",
      );
    } finally {
      rmSync(parent, { recursive: true, force: true });
    }
  });

  it("rejects reads and directory listings through symlinks outside the local root", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-workspace-"));
    const outside = mkdtempSync(join(tmpdir(), "autoctx-workspace-outside-"));
    const env = createLocalWorkspaceEnv({ root, cwd: "/repo" });

    try {
      await env.mkdir(".", { recursive: true });
      writeFileSync(join(outside, "secret.txt"), "outside\n", "utf-8");
      symlinkSync(outside, join(root, "repo", "outside"), "dir");

      await expect(env.readFile("outside/secret.txt")).rejects.toThrow(
        "Path escapes workspace root",
      );
      await expect(env.readFileBytes("outside/secret.txt")).rejects.toThrow(
        "Path escapes workspace root",
      );
      await expect(env.readdir("outside")).rejects.toThrow(
        "Path escapes workspace root",
      );
    } finally {
      rmSync(root, { recursive: true, force: true });
      rmSync(outside, { recursive: true, force: true });
    }
  });

  it("rejects writes and directory creation through symlinks outside the local root", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-workspace-"));
    const outside = mkdtempSync(join(tmpdir(), "autoctx-workspace-outside-"));
    const env = createLocalWorkspaceEnv({ root, cwd: "/repo" });

    try {
      await env.mkdir(".", { recursive: true });
      symlinkSync(outside, join(root, "repo", "outside"), "dir");

      await expect(env.writeFile("outside/escaped.txt", "escaped\n")).rejects.toThrow(
        "Path escapes workspace root",
      );
      await expect(env.mkdir("outside/escaped", { recursive: true })).rejects.toThrow(
        "Path escapes workspace root",
      );

      expect(existsSync(join(outside, "escaped.txt"))).toBe(false);
      expect(existsSync(join(outside, "escaped"))).toBe(false);
    } finally {
      rmSync(root, { recursive: true, force: true });
      rmSync(outside, { recursive: true, force: true });
    }
  });

  it("rejects writes through an outside-root file symlink", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-workspace-"));
    const outside = mkdtempSync(join(tmpdir(), "autoctx-workspace-outside-"));
    const env = createLocalWorkspaceEnv({ root, cwd: "/repo" });

    try {
      await env.mkdir(".", { recursive: true });
      const outsideFile = join(outside, "secret.txt");
      writeFileSync(outsideFile, "unchanged\n", "utf-8");
      symlinkSync(outsideFile, join(root, "repo", "secret-link"), "file");

      await expect(env.writeFile("secret-link", "overwritten\n")).rejects.toThrow(
        "Path escapes workspace root",
      );
      expect(readFileSync(outsideFile, "utf-8")).toBe("unchanged\n");
    } finally {
      rmSync(root, { recursive: true, force: true });
      rmSync(outside, { recursive: true, force: true });
    }
  });

  it("allows file operations through symlinks that remain inside the local root", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-workspace-"));
    const env = createLocalWorkspaceEnv({ root, cwd: "/repo" });

    try {
      await env.mkdir("target", { recursive: true });
      await env.writeFile("target/existing.txt", "inside\n");
      symlinkSync(join(root, "repo", "target"), join(root, "repo", "link"), "dir");

      expect(await env.readFile("link/existing.txt")).toBe("inside\n");
      await env.writeFile("link/written.txt", "written\n");
      await env.mkdir("link/nested", { recursive: true });

      expect(await env.readdir("link")).toEqual(["existing.txt", "nested", "written.txt"]);
      expect(readFileSync(join(root, "repo", "target", "written.txt"), "utf-8")).toBe(
        "written\n",
      );
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("rejects stat, exists, and removal through an outside-root parent symlink", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-workspace-"));
    const outside = mkdtempSync(join(tmpdir(), "autoctx-workspace-outside-"));
    const env = createLocalWorkspaceEnv({ root, cwd: "/repo" });

    try {
      await env.mkdir(".", { recursive: true });
      const outsideFile = join(outside, "keep.txt");
      writeFileSync(outsideFile, "unchanged\n", "utf-8");
      symlinkSync(outside, join(root, "repo", "outside"), "dir");

      await expect(env.stat("outside/keep.txt")).rejects.toThrow(
        "Path escapes workspace root",
      );
      await expect(env.exists("outside/keep.txt")).rejects.toThrow(
        "Path escapes workspace root",
      );
      await expect(env.rm("outside/keep.txt")).rejects.toThrow(
        "Path escapes workspace root",
      );

      expect(readFileSync(outsideFile, "utf-8")).toBe("unchanged\n");
    } finally {
      rmSync(root, { recursive: true, force: true });
      rmSync(outside, { recursive: true, force: true });
    }
  });

  it("can inspect and remove a final outside-pointing symlink without following it", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-workspace-"));
    const outside = mkdtempSync(join(tmpdir(), "autoctx-workspace-outside-"));
    const env = createLocalWorkspaceEnv({ root, cwd: "/repo" });

    try {
      await env.mkdir(".", { recursive: true });
      const outsideFile = join(outside, "keep.txt");
      writeFileSync(outsideFile, "unchanged\n", "utf-8");
      symlinkSync(outsideFile, join(root, "repo", "link"), "file");

      expect(await env.exists("link")).toBe(true);
      expect((await env.stat("link")).isSymbolicLink).toBe(true);

      await env.rm("link");

      expect(await env.exists("link")).toBe(false);
      expect(readFileSync(outsideFile, "utf-8")).toBe("unchanged\n");
    } finally {
      rmSync(root, { recursive: true, force: true });
      rmSync(outside, { recursive: true, force: true });
    }
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

  it("rejects an execution cwd reached through an outside-root symlink", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-workspace-"));
    const outside = mkdtempSync(join(tmpdir(), "autoctx-workspace-outside-"));
    const env = createLocalWorkspaceEnv({ root, cwd: "/repo" });

    try {
      await env.mkdir(".", { recursive: true });
      symlinkSync(outside, join(root, "repo", "outside"), "dir");

      await expect(env.exec("pwd", { cwd: "outside" })).rejects.toThrow(
        "Path escapes workspace root",
      );
    } finally {
      rmSync(root, { recursive: true, force: true });
      rmSync(outside, { recursive: true, force: true });
    }
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

  it("does not pass ambient host secrets into the fallback shell", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-workspace-"));
    const previous = process.env.AUTOCTX_HOST_SECRET;
    process.env.AUTOCTX_HOST_SECRET = "host-secret";
    try {
      const env = createLocalWorkspaceEnv({ root, cwd: "/repo" });
      await env.mkdir(".", { recursive: true });

      const result = await env.exec(
        `${JSON.stringify(process.execPath)} -e "process.stdout.write(process.env.AUTOCTX_HOST_SECRET ?? '')"`,
      );

      expect(result).toEqual({ stdout: "", stderr: "", exitCode: 0 });
    } finally {
      if (previous === undefined) {
        delete process.env.AUTOCTX_HOST_SECRET;
      } else {
        process.env.AUTOCTX_HOST_SECRET = previous;
      }
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("terminates fallback commands whose output exceeds the process cap", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-workspace-"));
    try {
      const env = createLocalWorkspaceEnv({ root, cwd: "/repo" });
      await env.mkdir(".", { recursive: true });
      const marker = join(root, "repo", "output-descendant-escaped.txt");
      await env.writeFile("output-descendant.cjs", `
const { writeFileSync } = require("node:fs");
process.on("SIGTERM", () => {});
setTimeout(() => writeFileSync(${JSON.stringify(marker)}, "escaped\\n"), 650);
setTimeout(() => process.exit(0), 1_300);
`);
      await env.writeFile("output-parent.cjs", `
const { spawn } = require("node:child_process");
process.on("SIGTERM", () => {});
spawn(process.execPath, ["output-descendant.cjs"], { cwd: __dirname, stdio: "ignore" });
process.stderr.write("pre-limit warning\\n" + "€".repeat(400_000));
process.stdout.write("€".repeat(400_000));
setTimeout(() => process.exit(0), 1_500);
`);

      const result = await env.exec(
        `${JSON.stringify(process.execPath)} output-parent.cjs`,
      );

      expect(result.exitCode).toBe(125);
      expect(Buffer.byteLength(result.stdout)).toBeLessThanOrEqual(1024 * 1024);
      expect(result.stdout).not.toContain("�");
      expect(Buffer.byteLength(result.stderr)).toBeLessThanOrEqual(1024 * 1024);
      expect(result.stderr).not.toContain("�");
      expect(result.stderr).toContain("pre-limit warning");
      expect(result.stderr).toContain("Command output exceeded the 1 MiB per-stream limit");
      await delay(750);
      expect(existsSync(marker)).toBe(false);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("kills fallback-shell descendants after a timeout", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-workspace-"));
    try {
      const env = createLocalWorkspaceEnv({ root, cwd: "/repo" });
      await env.mkdir(".", { recursive: true });
      const marker = join(root, "repo", "timeout-descendant-escaped.txt");
      await env.writeFile("timeout-descendant.cjs", `
const { writeFileSync } = require("node:fs");
process.on("SIGTERM", () => {});
setTimeout(() => writeFileSync(${JSON.stringify(marker)}, "escaped\\n"), 650);
setTimeout(() => process.exit(0), 1_300);
`);
      await env.writeFile("timeout-parent.cjs", `
const { spawn } = require("node:child_process");
spawn(process.execPath, ["timeout-descendant.cjs"], { cwd: __dirname, stdio: "ignore" });
setInterval(() => {}, 1_000);
`);

      const result = await env.exec(
        `${JSON.stringify(process.execPath)} timeout-parent.cjs`,
        { timeoutMs: 100 },
      );

      expect(result.exitCode).toBe(124);
      expect(result.stderr).toBe("Command timed out");
      await delay(750);
      expect(existsSync(marker)).toBe(false);
    } finally {
      rmSync(root, { recursive: true, force: true });
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
