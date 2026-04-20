import { describe, test, expect } from "vitest";
import {
  RATIONALE_PROMPT,
  FILE_OPT_OUT_TIP_PROMPT,
  SESSION_SUMMARY_PROMPT,
} from "../../../../src/control-plane/instrument/llm/prompts.js";

describe("RATIONALE_PROMPT", () => {
  test("includes file path, language, sdk, before + after snippets", () => {
    const out = RATIONALE_PROMPT({
      filePath: "src/chat.py",
      language: "python",
      sdkName: "openai",
      beforeSnippet: "client = OpenAI()",
      afterSnippet: "client = instrument_client(OpenAI())",
    });
    expect(out).toContain("src/chat.py");
    expect(out).toContain("python");
    expect(out).toContain("openai");
    expect(out).toContain("client = OpenAI()");
    expect(out).toContain("client = instrument_client(OpenAI())");
  });

  test("asks for 2-3 sentences and no markdown headings", () => {
    const out = RATIONALE_PROMPT({
      filePath: "x.ts",
      language: "typescript",
      sdkName: "anthropic",
      beforeSnippet: "a",
      afterSnippet: "b",
    });
    expect(out).toMatch(/2-3 sentences/i);
    expect(out).toMatch(/no markdown|no preamble|no closing/i);
  });
});

describe("FILE_OPT_OUT_TIP_PROMPT", () => {
  test("includes heuristic signals and mentions both opt-out mechanisms", () => {
    const out = FILE_OPT_OUT_TIP_PROMPT({
      filePath: "tests/test_llm.py",
      language: "python",
      heuristicSignals: ["looks-like-test-file"],
    });
    expect(out).toContain("tests/test_llm.py");
    expect(out).toContain("looks-like-test-file");
    expect(out).toMatch(/\.gitignore|--exclude/);
    expect(out).toMatch(/autocontext: off/);
  });

  test("handles empty heuristic signals list", () => {
    const out = FILE_OPT_OUT_TIP_PROMPT({
      filePath: "x.py",
      language: "python",
      heuristicSignals: [],
    });
    expect(out).toContain("none");
  });
});

describe("SESSION_SUMMARY_PROMPT", () => {
  test("includes counts and plugin list", () => {
    const out = SESSION_SUMMARY_PROMPT({
      filesAffected: 3,
      callSitesWrapped: 7,
      filesSkipped: 2,
      skippedBySecretLiteral: 1,
      registeredPluginIds: ["openai-python", "anthropic-ts"],
    });
    expect(out).toContain("3");
    expect(out).toContain("7");
    expect(out).toContain("openai-python");
    expect(out).toContain("anthropic-ts");
  });

  test("handles zero plugins gracefully", () => {
    const out = SESSION_SUMMARY_PROMPT({
      filesAffected: 0,
      callSitesWrapped: 0,
      filesSkipped: 0,
      skippedBySecretLiteral: 0,
      registeredPluginIds: [],
    });
    expect(out).toContain("(none)");
  });
});
