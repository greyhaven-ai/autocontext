/**
 * Tests for CLI DX improvements batch:
 * AC-393 (init), AC-405 (capabilities), AC-407 (login/whoami/logout),
 * AC-418 (version in capabilities), AC-420 (error formatting),
 * AC-421 (serve --json), AC-422 (list --json), AC-423 (replay info),
 * AC-424 (export-training-data progress).
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");
const SANITIZED_ENV_KEYS = [
  "ANTHROPIC_API_KEY",
  "OPENAI_API_KEY",
  "AUTOCONTEXT_API_KEY",
  "AUTOCONTEXT_AGENT_API_KEY",
  "AUTOCONTEXT_PROVIDER",
  "AUTOCONTEXT_AGENT_PROVIDER",
  "AUTOCONTEXT_MODEL",
  "AUTOCONTEXT_AGENT_DEFAULT_MODEL",
  "AUTOCONTEXT_AGENT_BASE_URL",
  "AUTOCONTEXT_BASE_URL",
  "AUTOCONTEXT_DB_PATH",
  "AUTOCONTEXT_RUNS_ROOT",
  "AUTOCONTEXT_KNOWLEDGE_ROOT",
  "AUTOCONTEXT_CONFIG_DIR",
];

function buildCliEnv(overrides: Record<string, string> = {}): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env, NODE_NO_WARNINGS: "1" };
  for (const key of SANITIZED_ENV_KEYS) {
    delete env[key];
  }
  return { ...env, ...overrides };
}

function runCli(
  args: string[],
  opts: { env?: Record<string, string>; cwd?: string; input?: string } = {},
): { stdout: string; stderr: string; exitCode: number } {
  const result = spawnSync("npx", ["tsx", CLI, ...args], {
    encoding: "utf8",
    timeout: 15000,
    cwd: opts.cwd,
    input: opts.input,
    env: buildCliEnv(opts.env),
  });
  return {
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    exitCode: result.status ?? 1,
  };
}

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-cli-dx-"));
}

function writeProjectConfig(dir: string, config: Record<string, unknown>): void {
  writeFileSync(join(dir, ".autoctx.json"), JSON.stringify(config, null, 2) + "\n", "utf-8");
}

async function runLongLivedCli(
  args: string[],
  opts: { env?: Record<string, string>; cwd?: string } = {},
): Promise<{ stdout: string; stderr: string; exitCode: number }> {
  return await new Promise((resolvePromise, rejectPromise) => {
    const child = spawn("npx", ["tsx", CLI, ...args], {
      cwd: opts.cwd,
      env: buildCliEnv(opts.env),
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

async function runCliAsync(
  args: string[],
  opts: { env?: Record<string, string>; cwd?: string; input?: string } = {},
): Promise<{ stdout: string; stderr: string; exitCode: number }> {
  return await new Promise((resolvePromise, rejectPromise) => {
    const child = spawn("npx", ["tsx", CLI, ...args], {
      cwd: opts.cwd,
      env: buildCliEnv(opts.env),
      stdio: ["pipe", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    const timeout = setTimeout(() => {
      child.kill("SIGTERM");
      rejectPromise(new Error(`Timed out waiting for CLI completion.\nstdout:\n${stdout}\nstderr:\n${stderr}`));
    }, 15000);

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk: string) => {
      stderr += chunk;
    });
    child.on("error", (err) => {
      clearTimeout(timeout);
      rejectPromise(err);
    });
    child.on("exit", (code, signal) => {
      clearTimeout(timeout);
      resolvePromise({
        stdout,
        stderr,
        exitCode: code ?? (signal ? 1 : 0),
      });
    });

    child.stdin.end(opts.input ?? "");
  });
}

async function runPromptedCli(
  args: string[],
  prompts: Array<{ when: string; answer: string }>,
  opts: { env?: Record<string, string>; cwd?: string } = {},
): Promise<{ stdout: string; stderr: string; exitCode: number }> {
  return await new Promise((resolvePromise, rejectPromise) => {
    const child = spawn("npx", ["tsx", CLI, ...args], {
      cwd: opts.cwd,
      env: buildCliEnv(opts.env),
      stdio: ["pipe", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    let promptIndex = 0;
    let stdinClosed = false;
    const timeout = setTimeout(() => {
      child.kill("SIGTERM");
      rejectPromise(new Error(`Timed out waiting for prompt flow.\nstdout:\n${stdout}\nstderr:\n${stderr}`));
    }, 15000);

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk: string) => {
      stderr += chunk;
      while (promptIndex < prompts.length && stderr.includes(prompts[promptIndex]!.when)) {
        child.stdin.write(prompts[promptIndex]!.answer);
        promptIndex += 1;
      }
      if (!stdinClosed && promptIndex >= prompts.length) {
        stdinClosed = true;
        child.stdin.end();
      }
    });
    child.on("error", (err) => {
      clearTimeout(timeout);
      rejectPromise(err);
    });
    child.on("exit", (code, signal) => {
      clearTimeout(timeout);
      resolvePromise({
        stdout,
        stderr,
        exitCode: code ?? (signal ? 1 : 0),
      });
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

  it("auto-detects provider/model defaults and can create AGENTS.md guidance", () => {
    const { exitCode } = runCli(["init", "--dir", dir, "--agents-md"], {
      env: {
        AUTOCONTEXT_AGENT_PROVIDER: "ollama",
        AUTOCONTEXT_AGENT_DEFAULT_MODEL: "llama3.2",
      },
    });
    expect(exitCode).toBe(0);

    const config = JSON.parse(readFileSync(join(dir, ".autoctx.json"), "utf-8"));
    expect(config.provider).toBe("ollama");
    expect(config.model).toBe("llama3.2");

    const agentsGuide = readFileSync(join(dir, "AGENTS.md"), "utf-8");
    expect(agentsGuide).toContain("## AutoContext");
    expect(agentsGuide).toContain("autoctx capabilities");
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

  it("includes project-specific config, active runs, and knowledge state when configured", async () => {
    const dir = makeTempDir();
    try {
      writeProjectConfig(dir, {
        default_scenario: "grid_ctf",
        provider: "deterministic",
        model: "fixture-model",
        gens: 4,
        runs_dir: "./runs",
        knowledge_dir: "./knowledge",
      });
      mkdirSync(join(dir, "runs"), { recursive: true });
      mkdirSync(join(dir, "knowledge", "lessons"), { recursive: true });
      writeFileSync(join(dir, "knowledge", "playbook.md"), "# Playbook\n", "utf-8");
      writeFileSync(join(dir, "knowledge", "lessons", "note.md"), "Keep pressure.\n", "utf-8");

      const { SQLiteStore } = await import("../src/storage/index.js");
      const store = new SQLiteStore(join(dir, "runs", "autocontext.sqlite3"));
      store.migrate(join(import.meta.dirname, "..", "migrations"));
      store.createRun("run-active", "grid_ctf", 2, "local", "deterministic");
      store.createRun("run-done", "grid_ctf", 1, "local", "deterministic");
      store.updateRunStatus("run-done", "completed");
      store.close();

      const { stdout, exitCode } = runCli(["capabilities"], { cwd: dir });
      expect(exitCode).toBe(0);

      const caps = JSON.parse(stdout);
      expect(caps.project_config).toBeTruthy();
      expect(caps.project_config.default_scenario).toBe("grid_ctf");
      expect(caps.project_config.provider).toBe("deterministic");
      expect(caps.project_config.model).toBe("fixture-model");
      expect(caps.project_config.active_runs).toBe(1);
      expect(caps.project_config.total_runs).toBe(2);
      expect(caps.project_config.knowledge_state).toEqual({
        exists: true,
        directories: 1,
        files: 2,
      });
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
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

  it("help includes logout command", () => {
    const { stdout } = runCli(["--help"]);
    expect(stdout).toContain("logout");
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
    const store = JSON.parse(readFileSync(join(configDir, "credentials.json"), "utf-8"));
    expect(store.providers.anthropic).toBeDefined();

    const whoami = JSON.parse(runCli(["whoami"], { env }).stdout);
    expect(whoami.provider).toBe("anthropic");
    expect(whoami.authenticated).toBe(true);

    const runResult = runCli(["run", "--scenario", "nonexistent_scenario_xyz"], { env });
    expect(runResult.stderr).toContain("Unknown scenario: nonexistent_scenario_xyz");
    expect(runResult.stderr).not.toContain("API key required");
  });

  it("login preserves command-based API key lookups instead of materializing them", () => {
    const configDir = join(dir, "config");
    const env = { AUTOCONTEXT_CONFIG_DIR: configDir };

    const { exitCode } = runCli(
      ["login", "--provider", "anthropic", "--key", "!echo sk-test-shell", "--config-dir", configDir],
      { env },
    );
    expect(exitCode).toBe(0);

    const store = JSON.parse(readFileSync(join(configDir, "credentials.json"), "utf-8"));
    expect(store.providers.anthropic.apiKey).toBe("!echo sk-test-shell");

    const runResult = runCli(["run", "--provider", "anthropic", "--scenario", "nonexistent_scenario_xyz"], { env });
    expect(runResult.stderr).toContain("Unknown scenario: nonexistent_scenario_xyz");
    expect(runResult.stderr).not.toContain("ANTHROPIC_API_KEY");
  });

  it("login supports interactive prompts when flags are omitted", async () => {
    const configDir = join(dir, "config");
    const { exitCode, stderr } = await runPromptedCli(["login", "--config-dir", configDir], [
      { when: "Provider:", answer: "anthropic\n" },
      { when: "API key:", answer: "sk-test-interactive\n" },
    ], {
      env: { AUTOCONTEXT_CONFIG_DIR: configDir },
    });

    expect(exitCode).toBe(0);
    expect(stderr).toContain("Provider:");
    expect(stderr).toContain("API key:");

    const store = JSON.parse(readFileSync(join(configDir, "credentials.json"), "utf-8"));
    expect(store.providers.anthropic).toBeDefined();
    expect(store.providers.anthropic.apiKey).toBe("sk-test-interactive");
  });

  it("validates Ollama connectivity and stores the normalized base URL", async () => {
    const configDir = join(dir, "config");
    const { createServer } = await import("node:http");
    const server = createServer((req, res) => {
      if (req.url === "/api/tags") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ models: [] }));
        return;
      }
      res.writeHead(404);
      res.end();
    });

    await new Promise<void>((resolve, reject) => {
      server.once("error", reject);
      server.listen(0, "127.0.0.1", () => resolve());
    });

    try {
      const address = server.address();
      if (!address || typeof address === "string") {
        throw new Error("Expected Ollama test server to bind to a TCP port");
      }

      const { exitCode, stdout } = await runCliAsync([
        "login",
        "--provider",
        "ollama",
        "--base-url",
        `http://127.0.0.1:${address.port}/v1/`,
        "--config-dir",
        configDir,
      ], {
        env: { AUTOCONTEXT_CONFIG_DIR: configDir },
      });

      expect(exitCode).toBe(0);
      expect(stdout).toContain(`Connected to Ollama at http://127.0.0.1:${address.port}`);

      const store = JSON.parse(readFileSync(join(configDir, "credentials.json"), "utf-8"));
      expect(store.providers.ollama).toBeDefined();
      expect(store.providers.ollama.baseUrl).toBe(`http://127.0.0.1:${address.port}`);
    } finally {
      await new Promise<void>((resolve, reject) => {
        server.close((err) => {
          if (err) {
            reject(err);
            return;
          }
          resolve();
        });
      });
    }
  });

  it("logout removes stored credentials", () => {
    const configDir = join(dir, "config");
    mkdirSync(configDir, { recursive: true });
    writeFileSync(join(configDir, "credentials.json"), JSON.stringify({
      provider: "anthropic",
      apiKey: "sk-test-logout",
    }, null, 2), "utf-8");

    const { stdout, exitCode } = runCli(["logout", "--config-dir", configDir], {
      env: { AUTOCONTEXT_CONFIG_DIR: configDir },
    });

    expect(exitCode).toBe(0);
    expect(stdout).toContain("Logged out from anthropic");
    expect(existsSync(join(configDir, "credentials.json"))).toBe(false);
  });

  it("uses environment variables before CLI provider flags", () => {
    const { stderr, exitCode } = runCli([
      "run",
      "--provider",
      "anthropic",
      "--scenario",
      "nonexistent_scenario_xyz",
    ], {
      env: { AUTOCONTEXT_AGENT_PROVIDER: "deterministic" },
    });

    expect(exitCode).toBe(1);
    expect(stderr).toContain("Unknown scenario: nonexistent_scenario_xyz");
    expect(stderr).not.toContain("ANTHROPIC_API_KEY");
  });

  it("uses CLI provider flags before project config", () => {
    writeProjectConfig(dir, {
      default_scenario: "grid_ctf",
      provider: "deterministic",
      runs_dir: "./runs",
      knowledge_dir: "./knowledge",
    });

    const { stderr, exitCode } = runCli([
      "run",
      "--provider",
      "anthropic",
      "--scenario",
      "nonexistent_scenario_xyz",
    ], { cwd: dir });

    expect(exitCode).toBe(1);
    expect(stderr).toContain("ANTHROPIC_API_KEY");
  });

  it("uses project config before the credential store", () => {
    const configDir = join(dir, "config");
    mkdirSync(configDir, { recursive: true });
    writeProjectConfig(dir, {
      default_scenario: "grid_ctf",
      provider: "deterministic",
      runs_dir: "./runs",
      knowledge_dir: "./knowledge",
    });
    writeFileSync(join(configDir, "credentials.json"), JSON.stringify({
      provider: "anthropic",
      apiKey: "sk-test-store",
    }, null, 2), "utf-8");

    const { stderr, exitCode } = runCli([
      "run",
      "--scenario",
      "nonexistent_scenario_xyz",
    ], {
      cwd: dir,
      env: { AUTOCONTEXT_CONFIG_DIR: configDir },
    });

    expect(exitCode).toBe(1);
    expect(stderr).toContain("Unknown scenario: nonexistent_scenario_xyz");
    expect(stderr).not.toContain("ANTHROPIC_API_KEY");
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

  it("reports the selected and available generations on stderr", () => {
    const dir = makeTempDir();
    try {
      const runsRoot = join(dir, "runs");
      const replayDir1 = join(runsRoot, "run-123", "generations", "gen_1", "replays");
      const replayDir3 = join(runsRoot, "run-123", "generations", "gen_3", "replays");
      mkdirSync(replayDir1, { recursive: true });
      mkdirSync(replayDir3, { recursive: true });

      const payload = {
        scenario: "grid_ctf",
        seed: 1003,
        narrative: "Blue secured the relay point.",
      };
      writeFileSync(join(replayDir1, "grid_ctf_1.json"), JSON.stringify({ scenario: "grid_ctf", seed: 1001 }), "utf-8");
      writeFileSync(join(replayDir3, "grid_ctf_3.json"), JSON.stringify(payload), "utf-8");

      const { stdout, stderr, exitCode } = runCli([
        "replay",
        "--run-id",
        "run-123",
        "--generation",
        "3",
      ], {
        env: { AUTOCONTEXT_RUNS_ROOT: runsRoot },
      });

      expect(exitCode).toBe(0);
      expect(stderr).toContain("Replaying generation 3. Available generations: 1, 3");
      expect(JSON.parse(stdout)).toEqual(payload);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
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

  it("prints progress to stderr while streaming JSONL to stdout", async () => {
    const dir = makeTempDir();
    try {
      const dbPath = join(dir, "runs", "autocontext.sqlite3");
      const runsRoot = join(dir, "runs");
      const knowledgeRoot = join(dir, "knowledge");
      mkdirSync(runsRoot, { recursive: true });

      const { SQLiteStore } = await import("../src/storage/index.js");
      const { ArtifactStore } = await import("../src/knowledge/artifact-store.js");
      const store = new SQLiteStore(dbPath);
      store.migrate(join(import.meta.dirname, "..", "migrations"));

      const artifacts = new ArtifactStore({ runsRoot, knowledgeRoot });
      artifacts.writePlaybook(
        "grid_ctf",
        [
          "# Strategy",
          "",
          "<!-- COMPETITOR_HINTS_START -->",
          "Flank early.",
          "<!-- COMPETITOR_HINTS_END -->",
        ].join("\n"),
      );
      store.createRun("cli-progress", "grid_ctf", 1, "local", "deterministic");
      store.upsertGeneration("cli-progress", 1, {
        meanScore: 0.61,
        bestScore: 0.72,
        elo: 1040,
        wins: 3,
        losses: 1,
        gateDecision: "advance",
        status: "completed",
      });
      store.appendAgentOutput("cli-progress", 1, "competitor", '{"aggression": 0.6}');
      store.close();

      const { stdout, stderr, exitCode } = runCli([
        "export-training-data",
        "--run-id",
        "cli-progress",
      ], {
        env: {
          AUTOCONTEXT_DB_PATH: dbPath,
          AUTOCONTEXT_RUNS_ROOT: runsRoot,
          AUTOCONTEXT_KNOWLEDGE_ROOT: knowledgeRoot,
        },
      });

      expect(exitCode).toBe(0);
      expect(stderr).toContain("Exporting training data for run cli-progress...");
      expect(stderr).toContain("Scanning 1 run(s)...");
      expect(stderr).toContain("Processed run cli-progress generation 1 (1 records)");
      expect(stderr).toContain("Exported 1 record(s).");

      const record = JSON.parse(stdout.trim());
      expect(record.run_id).toBe("cli-progress");
      expect(record.score).toBeCloseTo(0.72);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
