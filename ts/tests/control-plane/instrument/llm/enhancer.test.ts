import { describe, test, expect } from "vitest";
import fc from "fast-check";
import {
  enhance,
  type EnhancerProvider,
  type EnhancerDiagnostic,
} from "../../../../src/control-plane/instrument/llm/enhancer.js";

function mockProvider(impl: (prompt: string) => Promise<string>): EnhancerProvider {
  return { complete: ({ prompt }) => impl(prompt) };
}

describe("enhance", () => {
  test("returns defaultNarrative immediately when enabled=false; never calls provider", async () => {
    let called = false;
    const provider = mockProvider(async () => {
      called = true;
      return "should-not-be-used";
    });
    const out = await enhance({
      defaultNarrative: "default-text",
      context: {},
      prompt: () => "p",
      enabled: false,
      provider,
    });
    expect(out).toBe("default-text");
    expect(called).toBe(false);
  });

  test("returns LLM output when enabled and provider responds with non-empty text", async () => {
    const provider = mockProvider(async () => "  llm-enhanced text  ");
    const out = await enhance({
      defaultNarrative: "default",
      context: { foo: "bar" },
      prompt: (ctx: { foo: string }) => `prompt-${ctx.foo}`,
      enabled: true,
      provider,
    });
    expect(out).toBe("llm-enhanced text"); // trimmed
  });

  test("prompt function receives context", async () => {
    let received = "";
    const provider = mockProvider(async (prompt) => {
      received = prompt;
      return "ok";
    });
    await enhance({
      defaultNarrative: "d",
      context: { name: "test" },
      prompt: (ctx: { name: string }) => `hello ${ctx.name}`,
      enabled: true,
      provider,
    });
    expect(received).toBe("hello test");
  });

  test("falls back to default when provider throws; no throw propagates", async () => {
    const provider = mockProvider(async () => {
      throw new Error("upstream-fail");
    });
    const diagnostics: EnhancerDiagnostic[] = [];
    const out = await enhance({
      defaultNarrative: "fallback",
      context: {},
      prompt: () => "p",
      enabled: true,
      provider,
      onDiagnostic: (d) => diagnostics.push(d),
    });
    expect(out).toBe("fallback");
    expect(diagnostics.some((d) => d.kind === "provider-error")).toBe(true);
  });

  test("falls back when provider returns empty string", async () => {
    const provider = mockProvider(async () => "");
    const diagnostics: EnhancerDiagnostic[] = [];
    const out = await enhance({
      defaultNarrative: "default",
      context: {},
      prompt: () => "p",
      enabled: true,
      provider,
      onDiagnostic: (d) => diagnostics.push(d),
    });
    expect(out).toBe("default");
    expect(diagnostics.some((d) => d.kind === "malformed-output")).toBe(true);
  });

  test("falls back when provider returns whitespace-only", async () => {
    const provider = mockProvider(async () => "   \n\t  ");
    const out = await enhance({
      defaultNarrative: "default",
      context: {},
      prompt: () => "p",
      enabled: true,
      provider,
    });
    expect(out).toBe("default");
  });

  test("enabled=true but no provider → returns default with diagnostic", async () => {
    const diagnostics: EnhancerDiagnostic[] = [];
    const out = await enhance({
      defaultNarrative: "default",
      context: {},
      prompt: () => "p",
      enabled: true,
      onDiagnostic: (d) => diagnostics.push(d),
    });
    expect(out).toBe("default");
    expect(diagnostics.some((d) => d.kind === "no-provider")).toBe(true);
  });

  test("emits diagnostic: ok with char count on successful response", async () => {
    const provider = mockProvider(async () => "hello world");
    const diagnostics: EnhancerDiagnostic[] = [];
    await enhance({
      defaultNarrative: "d",
      context: {},
      prompt: () => "p",
      enabled: true,
      provider,
      onDiagnostic: (d) => diagnostics.push(d),
    });
    const ok = diagnostics.find((d) => d.kind === "ok") as Extract<EnhancerDiagnostic, { kind: "ok" }> | undefined;
    expect(ok?.chars).toBe("hello world".length);
  });

  test("times out and falls back when provider takes longer than timeoutMs", async () => {
    const provider: EnhancerProvider = {
      complete: () =>
        new Promise((resolve) => {
          setTimeout(() => resolve("late"), 500);
        }),
    };
    const diagnostics: EnhancerDiagnostic[] = [];
    const out = await enhance({
      defaultNarrative: "default",
      context: {},
      prompt: () => "p",
      enabled: true,
      provider,
      timeoutMs: 50,
      onDiagnostic: (d) => diagnostics.push(d),
    });
    expect(out).toBe("default");
    expect(diagnostics.some((d) => d.kind === "timeout")).toBe(true);
  });
});

describe("enhance — P-enhancer-never-throws property test", () => {
  test("always returns a string regardless of provider behavior (100 runs)", async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.constantFrom("ok", "throw", "empty", "whitespace", "long"),
        fc.string(),
        async (mode, defaultText) => {
          const provider: EnhancerProvider = {
            complete: async () => {
              if (mode === "throw") throw new Error("boom");
              if (mode === "empty") return "";
              if (mode === "whitespace") return "   \n  \t ";
              if (mode === "long") return "x".repeat(10_000);
              return "response";
            },
          };
          const out = await enhance({
            defaultNarrative: defaultText.length > 0 ? defaultText : "default",
            context: {},
            prompt: () => "p",
            enabled: true,
            provider,
          });
          expect(typeof out).toBe("string");
          expect(out.length).toBeGreaterThan(0);
        },
      ),
      { numRuns: 100 },
    );
  });

  test("when disabled, always returns defaultNarrative regardless of provider", async () => {
    await fc.assert(
      fc.asyncProperty(fc.constantFrom("ok", "throw"), async (mode) => {
        const provider: EnhancerProvider = {
          complete: async () => {
            if (mode === "throw") throw new Error("should not matter");
            return "ignored";
          },
        };
        const out = await enhance({
          defaultNarrative: "stable-default",
          context: {},
          prompt: () => "p",
          enabled: false,
          provider,
        });
        expect(out).toBe("stable-default");
      }),
      { numRuns: 50 },
    );
  });
});
