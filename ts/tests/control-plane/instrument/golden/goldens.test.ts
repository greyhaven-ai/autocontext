/**
 * A2-I Layer 9 — golden `pr-body.md` scenarios (spec §11.5).
 *
 * Four committed golden files validate the static (no-LLM) pr-body.md render
 * against hand-reviewed baselines. Diff-previewed on mismatch, never silently
 * overwritten. Regenerate with `UPDATE_GOLDEN=1 npm test`.
 *
 * All scenarios run with LLM enhancement OFF so goldens are byte-deterministic.
 */
import { describe, test, expect, beforeEach } from "vitest";
import {
  mkdtempSync,
  readFileSync,
  writeFileSync,
  rmSync,
  mkdirSync,
  existsSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { runInstrument } from "../../../../src/control-plane/instrument/pipeline/orchestrator.js";
import {
  registerDetectorPlugin,
  resetRegistryForTests,
} from "../../../../src/control-plane/instrument/registry/plugin-registry.js";
import {
  mockOpenAiPythonPlugin,
  mockAnthropicTsPlugin,
} from "../../../_fixtures/plugins/index.js";

const FIXED_ULID = "01HN0000000000000000000009";
const FIXED_NOW = "2026-04-19T12:00:00.000Z";
const VERSION = "0.0.0-golden";

const GOLDEN_DIR = join(
  dirname(fileURLToPath(import.meta.url)),
  "pr-bodies",
);
const UPDATE = process.env.UPDATE_GOLDEN === "1";

const scratches: string[] = [];
function scratch(): string {
  const d = mkdtempSync(join(tmpdir(), "a2i-golden-"));
  scratches.push(d);
  return d;
}

beforeEach(() => {
  resetRegistryForTests();
  while (scratches.length > 0) {
    const d = scratches.pop()!;
    try { rmSync(d, { recursive: true, force: true }); } catch { /* ignore */ }
  }
});

function assertGolden(scenarioName: string, actual: string): void {
  const goldenPath = join(GOLDEN_DIR, `${scenarioName}.md`);
  if (UPDATE || !existsSync(goldenPath)) {
    mkdirSync(GOLDEN_DIR, { recursive: true });
    writeFileSync(goldenPath, actual);
    if (!UPDATE) {
      throw new Error(
        `golden ${scenarioName}.md did not exist; wrote initial version. Re-run tests to verify.`,
      );
    }
    return;
  }
  const expected = readFileSync(goldenPath, "utf-8");
  if (actual !== expected) {
    // Preview first 40 lines of diff in the error message.
    const actualLines = actual.split("\n");
    const expectedLines = expected.split("\n");
    const diffPreview: string[] = [];
    const max = Math.min(Math.max(actualLines.length, expectedLines.length), 40);
    for (let i = 0; i < max; i++) {
      if (actualLines[i] !== expectedLines[i]) {
        diffPreview.push(`  line ${i + 1}:`);
        diffPreview.push(`    - expected: ${JSON.stringify(expectedLines[i] ?? "")}`);
        diffPreview.push(`    + actual:   ${JSON.stringify(actualLines[i] ?? "")}`);
      }
    }
    throw new Error(
      `golden mismatch for ${scenarioName}.md. Run with UPDATE_GOLDEN=1 to regenerate.\n`
      + diffPreview.slice(0, 60).join("\n"),
    );
  }
}

describe("golden pr-body scenarios (spec §11.5)", () => {
  test("empty-repo: zero detections", async () => {
    const cwd = scratch();
    mkdirSync(join(cwd, "src"), { recursive: true });
    writeFileSync(join(cwd, "src", "app.py"), "# empty app with no LLM calls\nprint('hello')\n");
    writeFileSync(join(cwd, ".gitignore"), "");

    // No plugins registered at all.
    const result = await runInstrument({
      cwd,
      mode: "dry-run",
      nowIso: FIXED_NOW,
      sessionUlid: FIXED_ULID,
      autoctxVersion: VERSION,
    });

    const prBody = readFileSync(join(result.sessionDir, "pr-body.md"), "utf-8");
    assertGolden("empty-repo", prBody);
  });

  test("one-plugin-one-file: single OpenAI-python detection", async () => {
    const cwd = scratch();
    mkdirSync(join(cwd, "src"), { recursive: true });
    writeFileSync(
      join(cwd, "src", "chat.py"),
      "from openai import OpenAI\nclient = OpenAI()\nresponse = client.chat.completions.create(model=\"gpt-4o\", messages=[])\n",
    );
    writeFileSync(join(cwd, ".gitignore"), "");
    registerDetectorPlugin(mockOpenAiPythonPlugin);

    const result = await runInstrument({
      cwd,
      mode: "dry-run",
      nowIso: FIXED_NOW,
      sessionUlid: FIXED_ULID,
      autoctxVersion: VERSION,
    });

    const prBody = readFileSync(join(result.sessionDir, "pr-body.md"), "utf-8");
    assertGolden("one-plugin-one-file", prBody);
  });

  test("multi-plugin: OpenAI-python + Anthropic-ts across 2 files", async () => {
    const cwd = scratch();
    mkdirSync(join(cwd, "src"), { recursive: true });
    writeFileSync(
      join(cwd, "src", "chat.py"),
      "from openai import OpenAI\nclient = OpenAI()\nresponse = client.chat.completions.create(model=\"gpt-4o\", messages=[])\n",
    );
    writeFileSync(
      join(cwd, "src", "support.ts"),
      "import Anthropic from '@anthropic-ai/sdk';\nconst client = new Anthropic();\nawait client.messages.create({ model: 'claude-sonnet-4-5', max_tokens: 100, messages: [] });\n",
    );
    writeFileSync(join(cwd, ".gitignore"), "");
    registerDetectorPlugin(mockOpenAiPythonPlugin);
    registerDetectorPlugin(mockAnthropicTsPlugin);

    const result = await runInstrument({
      cwd,
      mode: "dry-run",
      nowIso: FIXED_NOW,
      sessionUlid: FIXED_ULID,
      autoctxVersion: VERSION,
    });

    const prBody = readFileSync(join(result.sessionDir, "pr-body.md"), "utf-8");
    assertGolden("multi-plugin", prBody);
  });

  test("safety-skip: file with secret + file with off-file directive + excluded file + instrumented file", async () => {
    const cwd = scratch();
    mkdirSync(join(cwd, "src"), { recursive: true });
    mkdirSync(join(cwd, "tests"), { recursive: true });

    // (a) File with an AKIA-shaped secret literal → skipped by safety floor.
    writeFileSync(
      join(cwd, "src", "secrets_file.py"),
      "from openai import OpenAI\nAWS_ACCESS = 'AKIAIOSFODNN7EXAMPLE'\nclient = OpenAI()\n",
    );
    // (b) File with off-file directive → skipped.
    writeFileSync(
      join(cwd, "src", "opted_out.py"),
      "# autocontext: off-file\nfrom openai import OpenAI\nclient = OpenAI()\n",
    );
    // (c) Test file to exclude via --exclude flag.
    writeFileSync(
      join(cwd, "tests", "test_llm.py"),
      "from openai import OpenAI\nclient = OpenAI()\n",
    );
    // (d) File that instruments cleanly.
    writeFileSync(
      join(cwd, "src", "clean.py"),
      "from openai import OpenAI\nclient = OpenAI()\nresponse = client.chat.completions.create(model=\"gpt-4o\", messages=[])\n",
    );
    writeFileSync(join(cwd, ".gitignore"), "");
    registerDetectorPlugin(mockOpenAiPythonPlugin);

    const result = await runInstrument({
      cwd,
      mode: "dry-run",
      nowIso: FIXED_NOW,
      sessionUlid: FIXED_ULID,
      autoctxVersion: VERSION,
      exclude: ["tests/**"],
    });

    const prBody = readFileSync(join(result.sessionDir, "pr-body.md"), "utf-8");
    assertGolden("safety-skip", prBody);
  });
});
