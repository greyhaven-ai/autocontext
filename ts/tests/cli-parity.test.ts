/**
 * Tests for AC-363: CLI/package workflow parity — new commands.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";
import { SQLiteStore } from "../src/storage/index.js";
import { ArtifactStore } from "../src/knowledge/artifact-store.js";
import { HarnessStore } from "../src/knowledge/harness-store.js";

const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

function runCli(args: string[], envOverrides: Record<string, string> = {}): { stdout: string; exitCode: number } {
  try {
    const stdout = execFileSync("npx", ["tsx", CLI, ...args], {
      encoding: "utf8",
      timeout: 10000,
      env: { ...process.env, NODE_NO_WARNINGS: "1", ...envOverrides },
    });
    return { stdout, exitCode: 0 };
  } catch (err: unknown) {
    const e = err as { stdout?: string; status?: number };
    return { stdout: e.stdout ?? "", exitCode: e.status ?? 1 };
  }
}

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-cli-parity-"));
}

describe("CLI parity fixtures", () => {
  let dir: string;

  beforeEach(() => {
    dir = makeTempDir();
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("replay returns the persisted replay artifact payload", () => {
    const runsRoot = join(dir, "runs");
    const replayDir = join(runsRoot, "run-123", "generations", "gen_1", "replays");
    mkdirSync(replayDir, { recursive: true });
    const payload = {
      scenario: "grid_ctf",
      seed: 1000,
      narrative: "Blue captured the center lane.",
      timeline: [{ turn: 1, event: "move" }],
    };
    writeFileSync(join(replayDir, "grid_ctf_1.json"), JSON.stringify(payload, null, 2), "utf-8");

    const { stdout, exitCode } = runCli(["replay", "--run-id", "run-123"], {
      AUTOCONTEXT_RUNS_ROOT: runsRoot,
    });

    expect(exitCode).toBe(0);
    expect(JSON.parse(stdout)).toEqual(payload);
  });

  it("export returns real persisted package data instead of placeholders", () => {
    const dbPath = join(dir, "autocontext.db");
    const runsRoot = join(dir, "runs");
    const knowledgeRoot = join(dir, "knowledge");
    const skillsRoot = join(dir, "skills");

    const store = new SQLiteStore(dbPath);
    store.migrate(join(__dirname, "..", "migrations"));
    store.createRun("run-1", "grid_ctf", 1, "local", "deterministic");
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.71,
      bestScore: 0.83,
      elo: 1112.5,
      wins: 2,
      losses: 1,
      gateDecision: "advance",
      status: "completed",
    });
    store.recordMatch("run-1", 1, {
      seed: 1000,
      score: 0.83,
      passedValidation: true,
      validationErrors: "",
      winner: "challenger",
      strategyJson: JSON.stringify({ aggression: 0.8, flank_bias: 0.4 }),
      replayJson: JSON.stringify([{ turn: 1, lane: "center" }]),
    });
    store.updateRunStatus("run-1", "completed");
    store.close();

    const artifacts = new ArtifactStore({ runsRoot, knowledgeRoot });
    artifacts.writePlaybook(
      "grid_ctf",
      [
        "<!-- PLAYBOOK_START -->",
        "## Strategy Updates",
        "",
        "- Pressure center first.",
        "<!-- PLAYBOOK_END -->",
        "",
        "<!-- LESSONS_START -->",
        "- Stable wins came from balanced pressure.",
        "<!-- LESSONS_END -->",
        "",
        "<!-- COMPETITOR_HINTS_START -->",
        "- Keep defender coverage above 0.5.",
        "<!-- COMPETITOR_HINTS_END -->",
      ].join("\n"),
    );
    const harnessStore = new HarnessStore(knowledgeRoot, "grid_ctf");
    harnessStore.writeVersioned("validator", "def validate():\n    return True\n", 1);

    const { stdout, exitCode } = runCli(["export", "--scenario", "grid_ctf"], {
      AUTOCONTEXT_DB_PATH: dbPath,
      AUTOCONTEXT_RUNS_ROOT: runsRoot,
      AUTOCONTEXT_KNOWLEDGE_ROOT: knowledgeRoot,
      AUTOCONTEXT_SKILLS_ROOT: skillsRoot,
    });

    expect(exitCode).toBe(0);
    const parsed = JSON.parse(stdout);
    expect(parsed.best_score).toBeCloseTo(0.83);
    expect(parsed.best_elo).toBeCloseTo(1112.5);
    expect(parsed.best_strategy).toEqual({ aggression: 0.8, flank_bias: 0.4 });
    expect(parsed.lessons).toEqual(["Stable wins came from balanced pressure."]);
    expect(parsed.hints).toContain("Keep defender coverage above 0.5.");
    expect(parsed.harness.validator).toContain("def validate()");
    expect(parsed.skill_markdown).toContain("## Best Known Strategy");
  });

  it("import-package restores package metadata, harness, and skill markdown", () => {
    const runsRoot = join(dir, "runs");
    const knowledgeRoot = join(dir, "knowledge");
    const skillsRoot = join(dir, "skills");
    const pkgPath = join(dir, "grid_ctf_package.json");
    writeFileSync(
      pkgPath,
      JSON.stringify({
        format_version: 1,
        scenario_name: "grid_ctf",
        display_name: "Grid CTF",
        description: "Capture the flag strategy package.",
        playbook: "<!-- PLAYBOOK_START -->\nImported playbook\n<!-- PLAYBOOK_END -->",
        lessons: ["Preserve the high ground."],
        best_strategy: { aggression: 0.7 },
        best_score: 0.91,
        best_elo: 1234.5,
        hints: "Avoid overcommitting the left flank.",
        harness: {
          validator: "def validate():\n    return True\n",
        },
        metadata: {
          completed_runs: 3,
          has_snapshot: true,
          source_run_id: "run-imported",
        },
      }, null, 2),
      "utf-8",
    );

    const { stdout, exitCode } = runCli(["import-package", "--file", pkgPath, "--conflict", "overwrite"], {
      AUTOCONTEXT_RUNS_ROOT: runsRoot,
      AUTOCONTEXT_KNOWLEDGE_ROOT: knowledgeRoot,
      AUTOCONTEXT_SKILLS_ROOT: skillsRoot,
    });

    expect(exitCode).toBe(0);
    const parsed = JSON.parse(stdout);
    expect(parsed.playbookWritten).toBe(true);
    expect(parsed.skillWritten).toBe(true);
    expect(parsed.harnessWritten).toEqual(["validator"]);
    expect(parsed.metadataWritten).toBe(true);

    expect(readFileSync(join(knowledgeRoot, "grid_ctf", "playbook.md"), "utf-8")).toContain("Imported playbook");
    expect(readFileSync(join(knowledgeRoot, "grid_ctf", "package_metadata.json"), "utf-8")).toContain("\"best_score\": 0.91");
    expect(readFileSync(join(knowledgeRoot, "grid_ctf", "harness", "validator.py"), "utf-8")).toContain("def validate()");
    expect(readFileSync(join(skillsRoot, "grid-ctf-ops", "SKILL.md"), "utf-8")).toContain("# Grid CTF");
  });
});

// ---------------------------------------------------------------------------
// Help output includes all new commands
// ---------------------------------------------------------------------------

describe("CLI parity — help output", () => {
  it("help includes list command", () => {
    const { stdout } = runCli(["--help"]);
    expect(stdout).toContain("list");
  });

  it("help includes replay command", () => {
    const { stdout } = runCli(["--help"]);
    expect(stdout).toContain("replay");
  });

  it("help includes export command", () => {
    const { stdout } = runCli(["--help"]);
    expect(stdout).toContain("export");
  });

  it("help includes import-package command", () => {
    const { stdout } = runCli(["--help"]);
    expect(stdout).toContain("import-package");
  });

  it("help includes new-scenario command", () => {
    const { stdout } = runCli(["--help"]);
    expect(stdout).toContain("new-scenario");
  });

  it("help includes benchmark command", () => {
    const { stdout } = runCli(["--help"]);
    expect(stdout).toContain("benchmark");
  });
});

// ---------------------------------------------------------------------------
// list command
// ---------------------------------------------------------------------------

describe("CLI list command", () => {
  it("list returns JSON array", () => {
    const { stdout, exitCode } = runCli(["list", "--json"]);
    expect(exitCode).toBe(0);
    const parsed = JSON.parse(stdout);
    expect(Array.isArray(parsed)).toBe(true);
  });

  it("list --help shows options", () => {
    const { stdout, exitCode } = runCli(["list", "--help"]);
    expect(exitCode).toBe(0);
    expect(stdout).toContain("--json");
  });
});

// ---------------------------------------------------------------------------
// replay command
// ---------------------------------------------------------------------------

describe("CLI replay command", () => {
  it("replay --help shows usage", () => {
    const { stdout, exitCode } = runCli(["replay", "--help"]);
    expect(exitCode).toBe(0);
    expect(stdout).toContain("run-id");
    expect(stdout).toContain("generation");
  });

  it("replay requires run-id", () => {
    const { exitCode } = runCli(["replay"]);
    expect(exitCode).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// export command
// ---------------------------------------------------------------------------

describe("CLI export command", () => {
  it("export --help shows options", () => {
    const { stdout, exitCode } = runCli(["export", "--help"]);
    expect(exitCode).toBe(0);
    expect(stdout).toContain("scenario");
  });

  it("export requires scenario", () => {
    const { exitCode } = runCli(["export"]);
    expect(exitCode).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// import-package command
// ---------------------------------------------------------------------------

describe("CLI import-package command", () => {
  it("import-package --help shows options", () => {
    const { stdout, exitCode } = runCli(["import-package", "--help"]);
    expect(exitCode).toBe(0);
    expect(stdout).toContain("file");
    expect(stdout).toContain("conflict");
  });

  it("import-package requires file", () => {
    const { exitCode } = runCli(["import-package"]);
    expect(exitCode).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// new-scenario command
// ---------------------------------------------------------------------------

describe("CLI new-scenario command", () => {
  it("new-scenario --help shows options", () => {
    const { stdout, exitCode } = runCli(["new-scenario", "--help"]);
    expect(exitCode).toBe(0);
    expect(stdout).toContain("description");
  });

  it("new-scenario requires description", () => {
    const { exitCode } = runCli(["new-scenario"]);
    expect(exitCode).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// benchmark command
// ---------------------------------------------------------------------------

describe("CLI benchmark command", () => {
  it("benchmark --help shows options", () => {
    const { stdout, exitCode } = runCli(["benchmark", "--help"]);
    expect(exitCode).toBe(0);
    expect(stdout).toContain("scenario");
    expect(stdout).toContain("runs");
  });
});
