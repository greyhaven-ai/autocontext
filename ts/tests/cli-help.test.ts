/**
 * Tests for AC-403: Richer --help output with flag descriptions, examples, cross-references.
 */

import { describe, it, expect } from "vitest";
import { spawnSync } from "node:child_process";
import { join } from "node:path";

const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");

function runHelp(command: string): string {
  const args = command ? [command, "--help"] : ["--help"];
  const r = spawnSync("npx", ["tsx", CLI, ...args], {
    encoding: "utf8",
    timeout: 15000,
    env: { ...process.env, NODE_NO_WARNINGS: "1" },
  });
  return (r.stdout ?? "") + (r.stderr ?? "");
}

// ---------------------------------------------------------------------------
// Top-level help
// ---------------------------------------------------------------------------

describe("Top-level --help", () => {
  it("shows command list", () => {
    const out = runHelp("");
    expect(out).toContain("run");
    expect(out).toContain("list");
    expect(out).toContain("replay");
  });

  it("leads with plain-language paved-road examples", () => {
    const out = runHelp("");
    expect(out).toContain('autoctx solve "build an orbital transfer optimizer"');
    expect(out).toContain("autoctx show <run-id> --best");
    expect(out).toContain("autoctx watch <run-id>");
    expect(out).toContain("autoctx export <run-id>");
  });
});

// ---------------------------------------------------------------------------
// `run --help` — flag descriptions, examples, cross-references
// ---------------------------------------------------------------------------

describe("run --help", () => {
  const out = runHelp("run");

  it("includes flag descriptions", () => {
    expect(out).toContain("--scenario");
    expect(out).toContain("--gens");
    expect(out).toContain("--provider");
    expect(out).toContain("--matches");
  });

  it("describes what each flag does", () => {
    // Each flag should have a description, not just the flag name
    expect(out).toMatch(/--scenario\s+.{10,}/);
    expect(out).toMatch(/--gens\s+.{10,}/);
    expect(out).toMatch(/--provider\s+.{10,}/);
  });

  it("includes usage examples", () => {
    expect(out.toLowerCase()).toContain("example");
  });

  it("includes cross-references to related commands", () => {
    expect(out.toLowerCase()).toContain("see also");
    expect(out).toContain("list");
    expect(out).toContain("replay");
  });

  it("only uses runnable built-in scenario examples", () => {
    expect(out).toContain("grid_ctf");
    expect(out).not.toContain("othello");
    expect(out).not.toContain("resource_trader");
  });
});

// ---------------------------------------------------------------------------
// `list --help`
// ---------------------------------------------------------------------------

describe("list --help", () => {
  const out = runHelp("list");

  it("includes flag descriptions", () => {
    expect(out).toContain("--limit");
    expect(out).toContain("--scenario");
  });

  it("includes cross-references", () => {
    expect(out.toLowerCase()).toContain("see also");
  });

  it("documents the real default limit", () => {
    expect(out).toContain("default: 50");
  });
});

// ---------------------------------------------------------------------------
// `replay --help`
// ---------------------------------------------------------------------------

describe("replay --help", () => {
  const out = runHelp("replay");

  it("includes flag descriptions", () => {
    expect(out).toContain("--run-id");
    expect(out).toContain("--generation");
  });

  it("includes cross-references", () => {
    expect(out.toLowerCase()).toContain("see also");
  });

  it("documents the real default generation", () => {
    expect(out).toContain("default: 1");
  });
});

// ---------------------------------------------------------------------------
// `benchmark --help`
// ---------------------------------------------------------------------------

describe("benchmark --help", () => {
  const out = runHelp("benchmark");

  it("includes flag descriptions", () => {
    expect(out).toContain("--scenario");
    expect(out).toContain("--runs");
    expect(out).toContain("--gens");
  });

  it("includes usage examples", () => {
    expect(out.toLowerCase()).toContain("example");
  });
});

// ---------------------------------------------------------------------------
// `export --help`
// ---------------------------------------------------------------------------

describe("export --help", () => {
  const out = runHelp("export");

  it("includes flag descriptions", () => {
    expect(out).toContain("autoctx export <run-id>");
    expect(out).toContain("--scenario");
    expect(out).toContain("--output");
  });
});

// ---------------------------------------------------------------------------
// `mcp-serve --help`
// ---------------------------------------------------------------------------

describe("mcp-serve --help", () => {
  const out = runHelp("mcp-serve");

  it("documents exposed tools", () => {
    expect(out).toContain("evaluate_output");
    expect(out).toContain("run_improvement_loop");
    expect(out).toContain("queue_task");
    expect(out).not.toContain("autocontext_judge");
  });

  it("mentions stdio transport", () => {
    expect(out.toLowerCase()).toContain("stdio");
  });
});

// ---------------------------------------------------------------------------
// `login --help`
// ---------------------------------------------------------------------------

describe("login --help", () => {
  const out = runHelp("login");

  it("includes flag descriptions", () => {
    expect(out).toContain("--provider");
    expect(out).toContain("--key");
  });

  it("includes usage examples", () => {
    expect(out.toLowerCase()).toContain("example");
  });

  it("includes cross-references", () => {
    expect(out.toLowerCase()).toContain("see also");
  });
});

// ---------------------------------------------------------------------------
// `judge --help`
// ---------------------------------------------------------------------------

describe("judge --help", () => {
  const out = runHelp("judge");

  it("includes flag descriptions", () => {
    expect(out).toContain("--prompt");
    expect(out).toContain("--output");
    expect(out).toContain("--rubric");
  });

  it("includes usage examples", () => {
    expect(out.toLowerCase()).toContain("example");
  });
});

// ---------------------------------------------------------------------------
// `improve --help`
// ---------------------------------------------------------------------------

describe("improve --help", () => {
  const out = runHelp("improve");

  it("includes flag descriptions", () => {
    expect(out).toContain("--prompt");
    expect(out).toContain("--output");
    expect(out).toContain("--rubric");
  });

  it("includes cross-references", () => {
    expect(out.toLowerCase()).toContain("see also");
  });
});

// ---------------------------------------------------------------------------
// `init --help`
// ---------------------------------------------------------------------------

describe("init --help", () => {
  const out = runHelp("init");

  it("includes flag descriptions", () => {
    expect(out).toContain("--dir");
    expect(out).toContain("--scenario");
    expect(out).toContain("--provider");
  });

  it("includes usage examples", () => {
    expect(out.toLowerCase()).toContain("example");
  });

  it("matches the actual scaffolding behavior", () => {
    expect(out).toContain("AGENTS.md");
    expect(out).not.toContain("--agents-md");
    expect(out).not.toContain("othello");
  });
});
