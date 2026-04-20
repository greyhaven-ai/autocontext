import { describe, test, expect } from "vitest";
import {
  shouldEnableEnhancement,
  hasAnyLLMKey,
} from "../../../../src/control-plane/instrument/llm/tty-detector.js";

describe("shouldEnableEnhancement — spec §10.2 resolution order", () => {
  test("(1) CLI --enhanced forces on, trumping every other signal", () => {
    expect(
      shouldEnableEnhancement({
        cliEnhancedFlag: true,
        envAutoContextInstrumentLLM: "off",
        isStdoutTTY: false,
        hasLLMKey: false,
      }),
    ).toBe(true);
  });

  test("(2) Env LLM=off wins over TTY auto-enable", () => {
    expect(
      shouldEnableEnhancement({
        cliEnhancedFlag: false,
        envAutoContextInstrumentLLM: "off",
        isStdoutTTY: true,
        hasLLMKey: true,
      }),
    ).toBe(false);
  });

  test("(3) Env LLM=on forces on in CI (non-TTY)", () => {
    expect(
      shouldEnableEnhancement({
        cliEnhancedFlag: false,
        envAutoContextInstrumentLLM: "on",
        isStdoutTTY: false,
        hasLLMKey: true,
      }),
    ).toBe(true);
  });

  test("(3) Env LLM=on forces on even when no key present (surfaced as no-provider later)", () => {
    expect(
      shouldEnableEnhancement({
        cliEnhancedFlag: false,
        envAutoContextInstrumentLLM: "on",
        isStdoutTTY: false,
        hasLLMKey: false,
      }),
    ).toBe(true);
  });

  test("(4) TTY + key → auto-enable", () => {
    expect(
      shouldEnableEnhancement({
        cliEnhancedFlag: false,
        envAutoContextInstrumentLLM: undefined,
        isStdoutTTY: true,
        hasLLMKey: true,
      }),
    ).toBe(true);
  });

  test("(4) TTY without key → off (avoid calling a provider that isn't configured)", () => {
    expect(
      shouldEnableEnhancement({
        cliEnhancedFlag: false,
        envAutoContextInstrumentLLM: undefined,
        isStdoutTTY: true,
        hasLLMKey: false,
      }),
    ).toBe(false);
  });

  test("(5) CI (non-TTY) + key → off by default (CI shouldn't surprise-call LLMs)", () => {
    expect(
      shouldEnableEnhancement({
        cliEnhancedFlag: false,
        envAutoContextInstrumentLLM: undefined,
        isStdoutTTY: false,
        hasLLMKey: true,
      }),
    ).toBe(false);
  });

  test("(5) everything off → off", () => {
    expect(
      shouldEnableEnhancement({
        cliEnhancedFlag: false,
        envAutoContextInstrumentLLM: undefined,
        isStdoutTTY: false,
        hasLLMKey: false,
      }),
    ).toBe(false);
  });

  test("env var is trimmed and case-insensitive", () => {
    expect(
      shouldEnableEnhancement({
        cliEnhancedFlag: false,
        envAutoContextInstrumentLLM: "  OFF  ",
        isStdoutTTY: true,
        hasLLMKey: true,
      }),
    ).toBe(false);
    expect(
      shouldEnableEnhancement({
        cliEnhancedFlag: false,
        envAutoContextInstrumentLLM: "On",
        isStdoutTTY: false,
        hasLLMKey: false,
      }),
    ).toBe(true);
  });

  test("env var with unknown value falls through to TTY heuristic", () => {
    expect(
      shouldEnableEnhancement({
        cliEnhancedFlag: false,
        envAutoContextInstrumentLLM: "maybe",
        isStdoutTTY: true,
        hasLLMKey: true,
      }),
    ).toBe(true);
    expect(
      shouldEnableEnhancement({
        cliEnhancedFlag: false,
        envAutoContextInstrumentLLM: "maybe",
        isStdoutTTY: false,
        hasLLMKey: true,
      }),
    ).toBe(false);
  });
});

describe("hasAnyLLMKey", () => {
  test("true when ANTHROPIC_API_KEY set", () => {
    expect(hasAnyLLMKey({ ANTHROPIC_API_KEY: "sk-test" })).toBe(true);
  });

  test("true when AUTOCONTEXT_ANTHROPIC_API_KEY set", () => {
    expect(hasAnyLLMKey({ AUTOCONTEXT_ANTHROPIC_API_KEY: "sk-test" })).toBe(true);
  });

  test("true when AUTOCONTEXT_JUDGE_API_KEY set", () => {
    expect(hasAnyLLMKey({ AUTOCONTEXT_JUDGE_API_KEY: "sk-test" })).toBe(true);
  });

  test("true when OPENAI_API_KEY set", () => {
    expect(hasAnyLLMKey({ OPENAI_API_KEY: "sk-test" })).toBe(true);
  });

  test("false when none set", () => {
    expect(hasAnyLLMKey({})).toBe(false);
  });

  test("false when only unrelated vars set", () => {
    expect(hasAnyLLMKey({ HOME: "/root", PATH: "/bin" })).toBe(false);
  });
});
