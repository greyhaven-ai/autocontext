/**
 * A2-I Layer 8 — pr-body-renderer integration with LLM enhancer.
 *
 * Validates the critical reproducibility invariant (spec §5.4):
 *   `plan.json` is byte-identical whether LLM enhancement is enabled or not.
 *   `pr-body.md` MAY differ when enhancement is on; MUST be byte-identical
 *   when enhancement is off.
 */
import { describe, test, expect, beforeEach } from "vitest";
import fc from "fast-check";
import {
  mkdtempSync,
  readFileSync,
  rmSync,
  mkdirSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runInstrument } from "../../../../src/control-plane/instrument/pipeline/orchestrator.js";
import {
  registerDetectorPlugin,
  resetRegistryForTests,
} from "../../../../src/control-plane/instrument/registry/plugin-registry.js";
import { mockOpenAiPythonPlugin } from "../../../_fixtures/plugins/index.js";
import type { EnhancerProvider } from "../../../../src/control-plane/instrument/llm/enhancer.js";

const FIXED_ULID = "01HN0000000000000000000001";
const FIXED_NOW = "2026-04-17T12:00:00.000Z";

const scratches: string[] = [];
function scratch(): string {
  const d = mkdtempSync(join(tmpdir(), "a2i-prbody-llm-"));
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

function seedPythonFixture(dir: string): void {
  mkdirSync(join(dir, "src"), { recursive: true });
  writeFileSync(
    join(dir, "src", "chat.py"),
    "from openai import OpenAI\nclient = OpenAI()\nresponse = client.chat.completions.create(model=\"gpt-4o\", messages=[])\n",
  );
  writeFileSync(join(dir, ".gitignore"), "");
}

function staticProvider(text: string): EnhancerProvider {
  return { complete: async () => text };
}

describe("pr-body-renderer × enhancer integration", () => {
  test("enhancement disabled → pr-body.md matches the pre-Layer-8 deterministic template", async () => {
    const cwd = scratch();
    seedPythonFixture(cwd);
    registerDetectorPlugin(mockOpenAiPythonPlugin);

    const result = await runInstrument({
      cwd,
      mode: "dry-run",
      nowIso: FIXED_NOW,
      sessionUlid: FIXED_ULID,
      autoctxVersion: "0.0.0-test",
    });

    const prBody = readFileSync(join(result.sessionDir, "pr-body.md"), "utf-8");
    expect(prBody).toContain("Autocontext instrument");
    expect(prBody).toContain("### Summary by SDK");
    expect(prBody).toContain("### Files affected");
    expect(prBody).toContain("Rationale:");
    // Default-template language is present.
    expect(prBody).toContain("Autocontext trace");
  });

  test("enhancement enabled with static provider → LLM output reaches pr-body.md", async () => {
    const cwd = scratch();
    seedPythonFixture(cwd);
    registerDetectorPlugin(mockOpenAiPythonPlugin);

    const result = await runInstrument({
      cwd,
      mode: "dry-run",
      nowIso: FIXED_NOW,
      sessionUlid: FIXED_ULID,
      autoctxVersion: "0.0.0-test",
      enhanced: true,
      enhancementProvider: staticProvider("LLM-ENHANCED-NARRATIVE"),
    });

    const prBody = readFileSync(join(result.sessionDir, "pr-body.md"), "utf-8");
    expect(prBody).toContain("LLM-ENHANCED-NARRATIVE");
  });

  test("enhancement enabled but provider throws → pr-body.md falls back to defaults without throwing", async () => {
    const cwd = scratch();
    seedPythonFixture(cwd);
    registerDetectorPlugin(mockOpenAiPythonPlugin);

    const failingProvider: EnhancerProvider = {
      complete: async () => { throw new Error("upstream-failure"); },
    };

    const result = await runInstrument({
      cwd,
      mode: "dry-run",
      nowIso: FIXED_NOW,
      sessionUlid: FIXED_ULID,
      autoctxVersion: "0.0.0-test",
      enhanced: true,
      enhancementProvider: failingProvider,
    });

    const prBody = readFileSync(join(result.sessionDir, "pr-body.md"), "utf-8");
    expect(prBody).toContain("Rationale:");
    expect(prBody).not.toContain("upstream-failure");
  });

  test("CRITICAL: plan.json is byte-identical whether LLM enhancement is on or off", async () => {
    const runWith = async (enhanced: boolean) => {
      const cwd = scratch();
      seedPythonFixture(cwd);
      registerDetectorPlugin(mockOpenAiPythonPlugin);
      const result = await runInstrument({
        cwd,
        mode: "dry-run",
        nowIso: FIXED_NOW,
        sessionUlid: FIXED_ULID,
        autoctxVersion: "0.0.0-test",
        enhanced,
        enhancementProvider: staticProvider("different-text-each-time-" + Math.random()),
      });
      resetRegistryForTests();
      return readFileSync(join(result.sessionDir, "plan.json"), "utf-8");
    };

    const planOff = await runWith(false);
    const planOn = await runWith(true);
    expect(planOn).toBe(planOff);
  });
});

describe("P-plan-json-stable-across-llm-states (property test, 30 runs)", () => {
  test("plan.json byte-identical regardless of enhancement state or LLM content", async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.string({ minLength: 1, maxLength: 200 }),
        fc.boolean(),
        async (llmText, enhanced) => {
          const cwd = scratch();
          seedPythonFixture(cwd);
          resetRegistryForTests();
          registerDetectorPlugin(mockOpenAiPythonPlugin);
          const result = await runInstrument({
            cwd,
            mode: "dry-run",
            nowIso: FIXED_NOW,
            sessionUlid: FIXED_ULID,
            autoctxVersion: "0.0.0-test",
            enhanced,
            enhancementProvider: staticProvider(llmText),
          });
          const plan = readFileSync(join(result.sessionDir, "plan.json"), "utf-8");

          // Baseline run with enhancement off.
          const cwd2 = scratch();
          seedPythonFixture(cwd2);
          resetRegistryForTests();
          registerDetectorPlugin(mockOpenAiPythonPlugin);
          const result2 = await runInstrument({
            cwd: cwd2,
            mode: "dry-run",
            nowIso: FIXED_NOW,
            sessionUlid: FIXED_ULID,
            autoctxVersion: "0.0.0-test",
            enhanced: false,
          });
          const planBaseline = readFileSync(join(result2.sessionDir, "plan.json"), "utf-8");

          expect(plan).toBe(planBaseline);
        },
      ),
      { numRuns: 30 },
    );
  });
});
