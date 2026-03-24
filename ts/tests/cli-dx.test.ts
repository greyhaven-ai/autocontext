/**
 * Tests for CLI DX improvements batch:
 * AC-393 (init), AC-405 (capabilities), AC-407 (login/whoami/logout),
 * AC-418 (version in capabilities), AC-420 (error formatting),
 * AC-421 (serve --json), AC-422 (list --json), AC-423 (replay info),
 * AC-424 (export-training-data progress).
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { execFileSync, spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");

function runCli(args: string[], opts: { env?: Record<string, string>; cwd?: string } = {}): { stdout: string; stderr: string; exitCode: number } {
  try {
    const stdout = execFileSync("npx", ["tsx", CLI, ...args], {
      encoding: "utf8",
      timeout: 15000,
      cwd: opts.cwd,
      env: { ...process.env, NODE_NO_WARNINGS: "1", ...opts.env },
    });
    return { stdout, stderr: "", exitCode: 0 };
  } catch (err: unknown) {
    const e = err as { stdout?: string; stderr?: string; status?: number };
    return { stdout: e.stdout ?? "", stderr: e.stderr ?? "", exitCode: e.status ?? 1 };
  }
}

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-cli-dx-"));
}

async function runLongLivedCli(
  args: string[],
  opts: { env?: Record<string, string>; cwd?: string } = {},
): Promise<{ stdout: string; stderr: string; exitCode: number }> {
  return await new Promise((resolvePromise, rejectPromise) => {
    const child = spawn("npx", ["tsx", CLI, ...args], {
      cwd: opts.cwd,
      env: { ...process.env, NODE_NO_WARNINGS: "1", ...opts.env },
      stdio: ["ignore", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    let sawOutput = false;
    const timeout = setTimeout(() => {
      child.kill("SIGTERM");
      rejectPromise(new Error(`Timed out waiting for CLI output.\nstdout:\n${stdout}\nstderr:\n${stderr}`));
    }, 15000);

    const maybeStop = () => {
      if (!sawOutput && /[\r\n]/.test(stdout)) {
        sawOutput = true;
        child.kill("SIGTERM");
      }
    };

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk;
      maybeStop();
    });
    child.stderr.on("data", (chunk: string) => {
      stderr += chunk;
    });
    child.on("error", (err) => {
      clearTimeout(timeout);
      rejectPromise(err);
    });
    child.on("exit", (code) => {
      clearTimeout(timeout);
      if (sawOutput) {
        resolvePromise({ stdout, stderr, exitCode: code ?? 0 });
        return;
      }
      rejectPromise(new Error(`CLI exited before producing startup output.\nstdout:\n${stdout}\nstderr:\n${stderr}`));
    });
  });
}

// ---------------------------------------------------------------------------
// AC-393: autoctx init
// ---------------------------------------------------------------------------

describe("AC-393: autoctx init", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("help includes init command", () => {
    const { stdout } = runCli(["--help"]);
    expect(stdout).toContain("init");
  });

  it("creates .autoctx.json in the target directory", () => {
    const { exitCode } = runCli(["init", "--dir", dir]);
    expect(exitCode).toBe(0);
    expect(existsSync(join(dir, ".autoctx.json"))).toBe(true);
  });

  it("writes sensible defaults", () => {
    runCli(["init", "--dir", dir]);
    const config = JSON.parse(readFileSync(join(dir, ".autoctx.json"), "utf-8"));
    expect(config.default_scenario).toBeDefined();
    expect(config.provider).toBeDefined();
  });

  it("list uses configured runs_dir for the project database", () => {
    runCli(["init", "--dir", dir]);
    const configPath = join(dir, ".autoctx.json");
    const config = JSON.parse(readFileSync(configPath, "utf-8"));
    config.runs_dir = "./custom-runs";
    writeFileSync(configPath, JSON.stringify(config, null, 2) + "\n", "utf-8");

    const { stdout, exitCode } = runCli(["list", "--json"], { cwd: dir });
    expect(exitCode).toBe(0);
    expect(Array.isArray(JSON.parse(stdout))).toBe(true);
    expect(existsSync(join(dir, "custom-runs", "autocontext.sqlite3"))).toBe(true);
  });

  it("run falls back to default_scenario from .autoctx.json", () => {
    runCli(["init", "--dir", dir, "--scenario", "nonexistent_scenario_xyz"]);
    const { stderr, exitCode } = runCli(["run"], { cwd: dir });
    expect(exitCode).toBe(1);
    expect(stderr).toContain("Unknown scenario: nonexistent_scenario_xyz");
  });

  it("does not overwrite existing config", () => {
    runCli(["init", "--dir", dir]);
    const { exitCode, stderr } = runCli(["init", "--dir", dir]);
    expect(exitCode).toBe(1);
    expect(stderr).toContain("already exists");
  });
});

// ---------------------------------------------------------------------------
// AC-405: autoctx capabilities
// ---------------------------------------------------------------------------

describe("AC-405: autoctx capabilities", () => {
  it("help includes capabilities command", () => {
    const { stdout } = runCli(["--help"]);
    expect(stdout).toContain("capabilities");
  });

  it("returns structured JSON", () => {
    const { stdout, exitCode } = runCli(["capabilities"]);
    expect(exitCode).toBe(0);
    const caps = JSON.parse(stdout);
    expect(caps.version).toBeDefined();
    expect(caps.commands).toBeDefined();
    expect(caps.scenarios).toBeDefined();
    expect(caps.providers).toBeDefined();
    expect(Array.isArray(caps.commands)).toBe(true);
    expect(caps.commands).toContain("run");
  });
});

// ---------------------------------------------------------------------------
// AC-418: capabilities reports dynamic version
// ---------------------------------------------------------------------------

describe("AC-418: capabilities version", () => {
  it("version matches package.json", () => {
    const { stdout } = runCli(["capabilities"]);
    const caps = JSON.parse(stdout);
    const pkg = JSON.parse(readFileSync(join(import.meta.dirname, "..", "package.json"), "utf-8"));
    expect(caps.version).toBe(pkg.version);
  });
});

// ---------------------------------------------------------------------------
// AC-407: autoctx login/whoami/logout
// ---------------------------------------------------------------------------

describe("AC-407: credential management", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("help includes login command", () => {
    const { stdout } = runCli(["--help"]);
    expect(stdout).toContain("login");
  });

  it("help includes whoami command", () => {
    const { stdout } = runCli(["--help"]);
    expect(stdout).toContain("whoami");
  });

  it("whoami reports current provider status", () => {
    const { stdout, exitCode } = runCli(["whoami"], {
      env: { AUTOCONTEXT_AGENT_PROVIDER: "deterministic" },
    });
    expect(exitCode).toBe(0);
    expect(stdout).toContain("deterministic");
  });

  it("login persists credentials that whoami and run can reuse", () => {
    const configDir = join(dir, "config");
    const env = { AUTOCONTEXT_CONFIG_DIR: configDir };

    const { exitCode } = runCli(
      ["login", "--provider", "anthropic", "--key", "sk-test-123", "--config-dir", configDir],
      { env },
    );
    expect(exitCode).toBe(0);
    const creds = JSON.parse(readFileSync(join(configDir, "credentials.json"), "utf-8"));
    expect(creds.provider).toBe("anthropic");

    const whoami = JSON.parse(runCli(["whoami"], { env }).stdout);
    expect(whoami.provider).toBe("anthropic");
    expect(whoami.authenticated).toBe(true);

    const runResult = runCli(["run", "--scenario", "nonexistent_scenario_xyz"], { env });
    expect(runResult.stderr).toContain("Unknown scenario: nonexistent_scenario_xyz");
    expect(runResult.stderr).not.toContain("API key required");
  });
});

// ---------------------------------------------------------------------------
// AC-420: consistent error formatting
// ---------------------------------------------------------------------------

describe("AC-420: error formatting", () => {
  it("errors go to stderr as clean messages without stack traces", () => {
    const { stderr, exitCode } = runCli(["run", "--scenario", "nonexistent_scenario_xyz"]);
    expect(exitCode).toBe(1);
    // Should NOT contain raw stack traces
    expect(stderr).not.toContain("    at ");
    expect(stderr).not.toContain("node:internal");
  });
});

// ---------------------------------------------------------------------------
// AC-422: list --json
// ---------------------------------------------------------------------------

describe("AC-422: list --json", () => {
  it("list --json returns valid JSON array", () => {
    const { stdout, exitCode } = runCli(["list", "--json"]);
    expect(exitCode).toBe(0);
    const parsed = JSON.parse(stdout);
    expect(Array.isArray(parsed)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// AC-421: serve --json startup output
// ---------------------------------------------------------------------------

describe("AC-421: serve --json", () => {
  it("serve --help mentions --json flag", () => {
    const { stdout } = runCli(["serve", "--help"]);
    expect(stdout).toContain("--json");
  });

  it("serve --json emits machine-parseable startup metadata", async () => {
    const { stdout, stderr } = await runLongLivedCli(["serve", "--json", "--port", "0"]);
    expect(stderr).toBe("");

    const startup = JSON.parse(stdout.trim().split(/\r?\n/, 1)[0] ?? "");
    expect(startup.url).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/);
    expect(startup.apiUrl).toMatch(/^http:\/\/127\.0\.0\.1:\d+\/api\/runs$/);
    expect(startup.wsUrl).toMatch(/^ws:\/\/127\.0\.0\.1:\d+\/ws\/interactive$/);
    expect(startup.port).toBeGreaterThan(0);
    expect(Array.isArray(startup.scenarios)).toBe(true);
    expect(stdout).not.toContain("API:");
  });
});

// ---------------------------------------------------------------------------
// AC-423: replay shows which generation
// ---------------------------------------------------------------------------

describe("AC-423: replay generation info", () => {
  it("replay --help mentions generation default", () => {
    const { stdout } = runCli(["replay", "--help"]);
    expect(stdout).toContain("generation");
  });
});

// ---------------------------------------------------------------------------
// AC-424: export-training-data progress
// ---------------------------------------------------------------------------

describe("AC-424: export-training-data", () => {
  it("export-training-data --help mentions --output", () => {
    const { stdout } = runCli(["export-training-data", "--help"]);
    expect(stdout).toContain("--output");
  });
});
