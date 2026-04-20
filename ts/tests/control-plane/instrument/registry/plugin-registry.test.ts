/**
 * A2-I Layer 4 — plugin-registry (spec §7.2).
 *
 * Two public functions:
 *   - registerDetectorPlugin(plugin)
 *   - pluginsForLanguage(language) -> readonly DetectorPlugin[]
 *
 * Invariants (spec §4.4 I1 + §3.4 table):
 *   - Plugin.id is globally unique (duplicate id throws)
 *   - At most one plugin per (language, sdkName) pair (duplicate pair throws)
 *   - Same sdkName across different languages is allowed (e.g., openai-python + openai-ts)
 *   - Empty registry returns [] for any language (A2-I default)
 *   - resetRegistryForTests clears state (test-only helper)
 *
 * Property test P-registry-isolation: randomized register/reset sequences leave
 * the registry in consistent states (size matches last successful registrations).
 */
import { describe, test, expect, beforeEach } from "vitest";
import fc from "fast-check";
import {
  registerDetectorPlugin,
  pluginsForLanguage,
  resetRegistryForTests,
} from "../../../../src/control-plane/instrument/registry/plugin-registry.js";
import type {
  DetectorPlugin,
  InstrumentLanguage,
} from "../../../../src/control-plane/instrument/contract/plugin-interface.js";

function makePlugin(args: {
  readonly id: string;
  readonly language: InstrumentLanguage;
  readonly sdkName: string;
}): DetectorPlugin {
  return {
    id: args.id,
    supports: { language: args.language, sdkName: args.sdkName },
    treeSitterQueries: [],
    produce: () => [],
  };
}

beforeEach(() => {
  resetRegistryForTests();
});

describe("plugin-registry — empty default", () => {
  test("empty registry returns [] for every language", () => {
    const langs: InstrumentLanguage[] = ["python", "typescript", "javascript", "jsx", "tsx"];
    for (const l of langs) {
      expect(pluginsForLanguage(l)).toEqual([]);
    }
  });
});

describe("plugin-registry — register + retrieve", () => {
  test("register one plugin → pluginsForLanguage(lang) includes it", () => {
    const p = makePlugin({ id: "openai-python", language: "python", sdkName: "openai" });
    registerDetectorPlugin(p);
    const got = pluginsForLanguage("python");
    expect(got.length).toBe(1);
    expect(got[0]!.id).toBe("openai-python");
  });

  test("lookup by wrong language returns empty", () => {
    registerDetectorPlugin(
      makePlugin({ id: "openai-python", language: "python", sdkName: "openai" }),
    );
    expect(pluginsForLanguage("typescript")).toEqual([]);
  });

  test("multiple plugins for same language both returned", () => {
    registerDetectorPlugin(makePlugin({ id: "openai-python", language: "python", sdkName: "openai" }));
    registerDetectorPlugin(makePlugin({ id: "anthropic-python", language: "python", sdkName: "anthropic" }));
    const got = pluginsForLanguage("python");
    expect(got.map((p) => p.id).sort()).toEqual(["anthropic-python", "openai-python"]);
  });

  test("same sdkName across different languages both register", () => {
    registerDetectorPlugin(makePlugin({ id: "openai-python", language: "python", sdkName: "openai" }));
    registerDetectorPlugin(makePlugin({ id: "openai-ts", language: "typescript", sdkName: "openai" }));
    expect(pluginsForLanguage("python").map((p) => p.id)).toEqual(["openai-python"]);
    expect(pluginsForLanguage("typescript").map((p) => p.id)).toEqual(["openai-ts"]);
  });
});

describe("plugin-registry — conflict invariants", () => {
  test("duplicate id throws", () => {
    registerDetectorPlugin(makePlugin({ id: "openai-python", language: "python", sdkName: "openai" }));
    expect(() =>
      registerDetectorPlugin(
        makePlugin({ id: "openai-python", language: "javascript", sdkName: "something-else" }),
      ),
    ).toThrow(/duplicate plugin id.*openai-python/i);
  });

  test("duplicate (language, sdkName) pair throws", () => {
    registerDetectorPlugin(makePlugin({ id: "openai-python", language: "python", sdkName: "openai" }));
    expect(() =>
      registerDetectorPlugin(
        makePlugin({ id: "openai-python-v2", language: "python", sdkName: "openai" }),
      ),
    ).toThrow(/duplicate.*python.*openai/i);
  });

  test("registry state is NOT mutated by a failed registration", () => {
    registerDetectorPlugin(makePlugin({ id: "p1", language: "python", sdkName: "openai" }));
    expect(() =>
      registerDetectorPlugin(makePlugin({ id: "p1", language: "typescript", sdkName: "anthropic" })),
    ).toThrow();
    // The duplicate-id attempt must not have left a trace.
    expect(pluginsForLanguage("typescript")).toEqual([]);
    expect(pluginsForLanguage("python").map((p) => p.id)).toEqual(["p1"]);
  });
});

describe("plugin-registry — resetRegistryForTests", () => {
  test("clears state so subsequent lookups return []", () => {
    registerDetectorPlugin(makePlugin({ id: "p1", language: "python", sdkName: "openai" }));
    expect(pluginsForLanguage("python").length).toBe(1);
    resetRegistryForTests();
    expect(pluginsForLanguage("python")).toEqual([]);
  });
});

describe("plugin-registry — P-registry-isolation property (100 runs)", () => {
  test("size after N unique registrations equals N; reset empties", () => {
    fc.assert(
      fc.property(
        fc.uniqueArray(
          fc.tuple(
            fc.constantFrom<InstrumentLanguage>("python", "typescript", "javascript", "jsx", "tsx"),
            fc.stringMatching(/^[a-z][a-z0-9-]{1,12}$/),
          ),
          {
            // Uniqueness by (language, sdkName) tuple so every registration succeeds.
            selector: ([l, s]) => `${l}|${s}`,
            minLength: 0,
            maxLength: 12,
          },
        ),
        (tuples) => {
          resetRegistryForTests();
          for (let i = 0; i < tuples.length; i += 1) {
            const [language, sdkName] = tuples[i]!;
            registerDetectorPlugin(
              makePlugin({ id: `p-${i}-${language}-${sdkName}`, language, sdkName }),
            );
          }
          const total = (["python", "typescript", "javascript", "jsx", "tsx"] as const)
            .map((l) => pluginsForLanguage(l).length)
            .reduce((a, b) => a + b, 0);
          if (total !== tuples.length) return false;
          resetRegistryForTests();
          const afterReset = (["python", "typescript", "javascript", "jsx", "tsx"] as const)
            .map((l) => pluginsForLanguage(l).length)
            .reduce((a, b) => a + b, 0);
          return afterReset === 0;
        },
      ),
      { numRuns: 100 },
    );
  });
});
