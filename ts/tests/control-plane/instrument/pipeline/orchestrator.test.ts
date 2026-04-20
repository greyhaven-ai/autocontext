/**
 * A2-I Layer 6 - orchestrator end-to-end tests.
 *
 * Spins up a tiny fixture repo in a scratch directory, registers a fixture
 * plugin, runs `runInstrument` in dry-run mode, and asserts the session
 * directory shape + InstrumentResult fields.
 */
import { describe, test, expect, beforeEach } from "vitest";
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  readdirSync,
  existsSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runInstrument } from "../../../../src/control-plane/instrument/pipeline/orchestrator.js";
import {
  registerDetectorPlugin,
  resetRegistryForTests,
} from "../../../../src/control-plane/instrument/registry/plugin-registry.js";
import {
  mockOpenAiPythonPlugin,
  mockConflictingPlugin,
  mockAnthropicTsPlugin,
} from "../../../_fixtures/plugins/index.js";

const FIXED_ULID = "01HN0000000000000000000001";
const FIXED_NOW = "2026-04-17T12:00:00.000Z";

const scratches: string[] = [];
function scratch(): string {
  const d = mkdtempSync(join(tmpdir(), "a2i-orch-"));
  scratches.push(d);
  return d;
}

beforeEach(() => {
  resetRegistryForTests();
  while (scratches.length > 0) {
    const d = scratches.pop()!;
    try {
      rmSync(d, { recursive: true, force: true });
    } catch {
      // ignore
    }
  }
});

function seedPythonRepo(root: string): void {
  mkdirSync(join(root, "src"), { recursive: true });
  writeFileSync(
    join(root, "src", "main.py"),
    [
      "import os",
      "from openai import OpenAI",
      "",
      "client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))",
      "",
    ].join("\n"),
    "utf-8",
  );
  // A .gitignore to exercise the fingerprint hash.
  writeFileSync(join(root, ".gitignore"), "dist/\n*.log\n", "utf-8");
}

describe("runInstrument - dry-run happy path", () => {
  test("empty registry + no plugins -> exit 0, zero affected files, session dir written", async () => {
    const cwd = scratch();
    seedPythonRepo(cwd);

    const result = await runInstrument({
      cwd,
      mode: "dry-run",
      sessionUlid: FIXED_ULID,
      nowIso: FIXED_NOW,
    });
    expect(result.exitCode).toBe(0);
    expect(result.filesAffected).toBe(0);
    expect(result.filesScanned).toBeGreaterThan(0);
    // Session dir with canonical layout.
    const sessionDir = join(cwd, ".autocontext", "instrument-patches", FIXED_ULID);
    expect(existsSync(join(sessionDir, "session.json"))).toBe(true);
    expect(existsSync(join(sessionDir, "plan.json"))).toBe(true);
    expect(existsSync(join(sessionDir, "detections.jsonl"))).toBe(true);
    expect(existsSync(join(sessionDir, "pr-body.md"))).toBe(true);
    expect(existsSync(join(sessionDir, "patches"))).toBe(true);
  });

  test("with mock-openai-python registered -> one affected file + one patch", async () => {
    registerDetectorPlugin(mockOpenAiPythonPlugin);
    const cwd = scratch();
    seedPythonRepo(cwd);

    const result = await runInstrument({
      cwd,
      mode: "dry-run",
      sessionUlid: FIXED_ULID,
      nowIso: FIXED_NOW,
    });
    expect(result.exitCode).toBe(0);
    expect(result.filesAffected).toBe(1);
    expect(result.callSitesDetected).toBe(1);

    const sessionDir = join(cwd, ".autocontext", "instrument-patches", FIXED_ULID);
    const patchesDir = join(sessionDir, "patches");
    const files = readdirSync(patchesDir);
    expect(files.length).toBe(1);
    expect(files[0]).toMatch(/^0001\..*\.patch$/);

    const patchBody = readFileSync(join(patchesDir, files[0]!), "utf-8");
    expect(patchBody).toContain("instrument_client(OpenAI(api_key=os.getenv('OPENAI_API_KEY')))");

    const prBody = readFileSync(join(sessionDir, "pr-body.md"), "utf-8");
    expect(prBody).toContain("files affected");
    expect(prBody).toContain("Session:");
    expect(prBody).toContain(FIXED_ULID);
    expect(prBody).toContain("Autocontext instrument");
    expect(prBody).toContain("Audit fingerprint");
  });
});

describe("runInstrument - plan.json determinism (P-session-determinism foundation)", () => {
  test("same inputs produce byte-identical plan.json across runs", async () => {
    registerDetectorPlugin(mockOpenAiPythonPlugin);
    const cwd = scratch();
    seedPythonRepo(cwd);

    const r1 = await runInstrument({
      cwd,
      mode: "dry-run",
      sessionUlid: FIXED_ULID,
      nowIso: FIXED_NOW,
    });
    const plan1 = readFileSync(join(r1.sessionDir, "plan.json"), "utf-8");

    // Same inputs again - with same plugin + same fixture - should reproduce bytes.
    const cwd2 = scratch();
    seedPythonRepo(cwd2);
    const r2 = await runInstrument({
      cwd: cwd2,
      mode: "dry-run",
      sessionUlid: FIXED_ULID,
      nowIso: FIXED_NOW,
    });
    const plan2 = readFileSync(join(r2.sessionDir, "plan.json"), "utf-8");
    expect(plan2).toBe(plan1);
    expect(r2.planHash).toBe(r1.planHash);
  });
});

describe("runInstrument - conflict exit 13", () => {
  test("two plugins wrapping same range with different wrapFn -> exit 13", async () => {
    registerDetectorPlugin(mockOpenAiPythonPlugin);
    registerDetectorPlugin(mockConflictingPlugin);
    const cwd = scratch();
    seedPythonRepo(cwd);

    const result = await runInstrument({
      cwd,
      mode: "dry-run",
      sessionUlid: FIXED_ULID,
      nowIso: FIXED_NOW,
    });
    expect(result.exitCode).toBe(13);
    expect(result.conflicts.length).toBe(1);
    expect(result.conflicts[0]!.kind).toBe("same-range-different-wrapfn");
    // Session dir STILL written so developers can inspect.
    expect(existsSync(join(result.sessionDir, "plan.json"))).toBe(true);
  });
});

describe("runInstrument - P-mode-isolation invariant (dry-run writes only to session dir)", () => {
  test("dry-run never writes outside .autocontext/instrument-patches/<ulid>/", async () => {
    registerDetectorPlugin(mockOpenAiPythonPlugin);
    const cwd = scratch();
    seedPythonRepo(cwd);

    const before = readFileSync(join(cwd, "src", "main.py"), "utf-8");
    await runInstrument({
      cwd,
      mode: "dry-run",
      sessionUlid: FIXED_ULID,
      nowIso: FIXED_NOW,
    });
    const after = readFileSync(join(cwd, "src", "main.py"), "utf-8");
    expect(after).toBe(before);
  });
});

describe("runInstrument - preflight failure propagates exit code", () => {
  test("--fail-if-empty with no plugins -> exit 12", async () => {
    const cwd = scratch();
    seedPythonRepo(cwd);
    const result = await runInstrument({
      cwd,
      mode: "dry-run",
      sessionUlid: FIXED_ULID,
      nowIso: FIXED_NOW,
      failIfEmpty: true,
    });
    expect(result.exitCode).toBe(12);
  });

  test("unreadable excludeFrom -> exit 11", async () => {
    const cwd = scratch();
    seedPythonRepo(cwd);
    const result = await runInstrument({
      cwd,
      mode: "dry-run",
      sessionUlid: FIXED_ULID,
      nowIso: FIXED_NOW,
      excludeFrom: join(cwd, "missing.txt"),
    });
    expect(result.exitCode).toBe(11);
  });

  test("nonexistent cwd -> exit 14", async () => {
    const result = await runInstrument({
      cwd: "/definitely/nonexistent/path/for/a2i-test",
      mode: "dry-run",
      sessionUlid: FIXED_ULID,
      nowIso: FIXED_NOW,
    });
    expect(result.exitCode).toBe(14);
  });
});

describe("runInstrument - TypeScript plugin through the same pipeline", () => {
  test("mock-anthropic-ts detects a new Anthropic() call", async () => {
    registerDetectorPlugin(mockAnthropicTsPlugin);
    const cwd = scratch();
    mkdirSync(join(cwd, "src"), { recursive: true });
    writeFileSync(
      join(cwd, "src", "app.ts"),
      [
        'import { Anthropic } from "@anthropic-ai/sdk";',
        "",
        'const client = new Anthropic({ apiKey: "placeholder" });',
        "export { client };",
        "",
      ].join("\n"),
      "utf-8",
    );

    const result = await runInstrument({
      cwd,
      mode: "dry-run",
      sessionUlid: FIXED_ULID,
      nowIso: FIXED_NOW,
    });
    expect(result.exitCode).toBe(0);
    expect(result.filesAffected).toBe(1);
  });
});
