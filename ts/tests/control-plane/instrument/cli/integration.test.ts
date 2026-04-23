/**
 * A2-I Layer 7 - CLI integration tests.
 *
 * Full argv -> session-dir flow, using fixture plugins to drive real edit
 * composition + patch emission.
 */
import { describe, test, expect, beforeEach } from "vitest";
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  existsSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runInstrumentCommand } from "../../../../src/control-plane/instrument/cli/runner.js";
import {
  registerDetectorPlugin,
  resetRegistryForTests,
} from "../../../../src/control-plane/instrument/registry/plugin-registry.js";
import {
  mockOpenAiPythonPlugin,
  mockAnthropicTsPlugin,
  mockInsertStatementPlugin,
} from "../../../_fixtures/plugins/index.js";

const ULID = "01HN0000000000000000000001";
const NOW = "2026-04-17T12:00:00.000Z";

const scratches: string[] = [];
function scratch(): string {
  const d = mkdtempSync(join(tmpdir(), "a2i-int-"));
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

describe("CLI -> session-dir integration", () => {
  test("Python + openai detector produces a wrapped patch", async () => {
    registerDetectorPlugin(mockOpenAiPythonPlugin);
    const cwd = scratch();
    mkdirSync(join(cwd, "src"), { recursive: true });
    writeFileSync(
      join(cwd, "src", "main.py"),
      "from openai import OpenAI\nclient = OpenAI(api_key='placeholder')\n",
      "utf-8",
    );

    const r = await runInstrumentCommand(["--output", "json"], {
      cwd,
      nowIso: NOW,
      sessionUlid: ULID,
    });
    expect(r.exitCode).toBe(0);
    const payload = JSON.parse(r.stdout);
    expect(payload.filesAffected).toBe(1);
    expect(payload.callSitesDetected).toBe(1);
    const sessionDir = join(cwd, ".autocontext", "instrument-patches", ULID);
    expect(existsSync(join(sessionDir, "plan.json"))).toBe(true);
    const prBody = readFileSync(join(sessionDir, "pr-body.md"), "utf-8");
    expect(prBody).toContain("openai");
    expect(prBody).toContain("Session:");
  });

  test("InsertStatement plugin composes through pipeline", async () => {
    registerDetectorPlugin(mockInsertStatementPlugin);
    const cwd = scratch();
    mkdirSync(join(cwd, "src"), { recursive: true });
    writeFileSync(
      join(cwd, "src", "main.py"),
      "# ANCHOR_HERE\nprint('hi')\n",
      "utf-8",
    );
    const r = await runInstrumentCommand(["--output", "json"], {
      cwd,
      nowIso: NOW,
      sessionUlid: ULID,
    });
    expect(r.exitCode).toBe(0);
    const payload = JSON.parse(r.stdout);
    expect(payload.filesAffected).toBe(1);
  });

  test("Multiple plugins across languages - JSON output lists sessionDir + planHash", async () => {
    registerDetectorPlugin(mockOpenAiPythonPlugin);
    registerDetectorPlugin(mockAnthropicTsPlugin);
    const cwd = scratch();
    mkdirSync(join(cwd, "src"), { recursive: true });
    writeFileSync(
      join(cwd, "src", "a.py"),
      "from openai import OpenAI\nc = OpenAI()\n",
      "utf-8",
    );
    writeFileSync(
      join(cwd, "src", "b.ts"),
      'import { Anthropic } from "@anthropic-ai/sdk"; const c = new Anthropic({});',
      "utf-8",
    );
    const r = await runInstrumentCommand(["--output", "json"], {
      cwd,
      nowIso: NOW,
      sessionUlid: ULID,
    });
    expect(r.exitCode).toBe(0);
    const payload = JSON.parse(r.stdout);
    expect(payload.filesAffected).toBe(2);
    expect(payload.sessionDir).toContain(ULID);
    expect(payload.planHash).toMatch(/^sha256:[0-9a-f]{64}$/);
  });
});
