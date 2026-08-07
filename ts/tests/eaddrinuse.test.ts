/**
 * Tests for AC-419: EADDRINUSE crash — graceful port handling.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { execFileSync } from "node:child_process";
import { createServer } from "node:http";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const CLI = join(__dirname, "..", "src", "cli", "index.ts");

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-eaddrinuse-"));
}

function runCli(args: string[], envOverrides: Record<string, string> = {}): { stdout: string; stderr: string; exitCode: number } {
  try {
    const stdout = execFileSync("npx", ["tsx", CLI, ...args], {
      cwd: join(__dirname, ".."),
      encoding: "utf8",
      timeout: 10000,
      env: { ...process.env, NODE_NO_WARNINGS: "1", ...envOverrides },
    });
    return { stdout, stderr: "", exitCode: 0 };
  } catch (err: unknown) {
    const e = err as { stdout?: string; stderr?: string; status?: number };
    return { stdout: e.stdout ?? "", stderr: e.stderr ?? "", exitCode: e.status ?? 1 };
  }
}

describe("EADDRINUSE handling", () => {
  let dir: string;
  let blocker: ReturnType<typeof createServer>;
  let blockerPort: number;

  beforeEach(async () => {
    dir = makeTempDir();
    // Start a blocker server on a random port
    blocker = createServer((_req, res) => {
      res.writeHead(200);
      res.end("blocker");
    });
    await new Promise<void>((resolve) => {
      blocker.listen(0, "127.0.0.1", () => {
        const addr = blocker.address();
        blockerPort = typeof addr === "object" && addr ? addr.port : 0;
        resolve();
      });
    });
  });

  afterEach(async () => {
    await new Promise<void>((resolve) => blocker.close(() => resolve()));
    rmSync(dir, { recursive: true, force: true });
  });

  it("serve prints a clean port-in-use error without a raw Node stack trace", () => {
    const { stderr, exitCode } = runCli(
      ["serve", "--port", String(blockerPort)],
      {
        AUTOCONTEXT_DB_PATH: join(dir, "test.db"),
        AUTOCONTEXT_RUNS_ROOT: join(dir, "runs"),
        AUTOCONTEXT_KNOWLEDGE_ROOT: join(dir, "knowledge"),
        AUTOCONTEXT_AGENT_PROVIDER: "deterministic",
      },
    );

    expect(exitCode).toBe(1);
    expect(stderr).toContain(String(blockerPort));
    expect(stderr).toContain("already in use");
    expect(stderr).toContain("--port");
    expect(stderr).not.toContain("EADDRINUSE");
    expect(stderr).not.toContain("setupListenHandle");
    expect(stderr).not.toContain("node:net");
  });

  it("port 0 still works (auto-assign)", async () => {
    const { RunManager, InteractiveServer } = await import("../src/server/index.js");
    const { SQLiteStore } = await import("../src/storage/index.js");
    const { asDbPath } = await import("../src/domain/ids.js");

    const dbPath = join(dir, "test.db");
    const store = new SQLiteStore(asDbPath(dbPath));
    store.migrate(join(__dirname, "..", "migrations"));
    store.close();

    const mgr = new RunManager({
      dbPath,
      migrationsDir: join(__dirname, "..", "migrations"),
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
      providerType: "deterministic",
    });

    const server = new InteractiveServer({ runManager: mgr, port: 0 });
    await server.start();
    expect(server.port).toBeGreaterThan(0);
    await server.stop();
  });
});
