/**
 * A2-I Layer 3 — directive-parser (extracted from scanner/source-file.ts).
 *
 * Layers 1+2 shipped the parser inline in source-file.ts. Layer 3 extracts
 * the parse function into `safety/directive-parser.ts` as the canonical home
 * (safety is the bounded context that owns directive semantics per spec §3.4).
 *
 * This test file covers the safety-layer API surface and adds P-directive-
 * coverage property tests. It exercises the `parseDirectives(bytes, language)`
 * (Buffer) form; source-file.ts now adapts bytes->lines internally.
 */
import { describe, test, expect } from "vitest";
import fc from "fast-check";
import { parseDirectives } from "../../../../src/control-plane/instrument/safety/directive-parser.js";

function buf(s: string): Buffer {
  return Buffer.from(s, "utf-8");
}

describe("parseDirectives — Python", () => {
  test("`# autocontext: off` at line N marks line N+1", () => {
    const bytes = buf(["import x", "# autocontext: off", "y = 1"].join("\n"));
    const map = parseDirectives(bytes, "python");
    expect(map.get(3)).toBe("off");
    expect(map.size).toBe(1);
  });

  test("`off-file` applies from its own line", () => {
    const bytes = buf(
      ["import x", "# autocontext: off-file", "y = 1"].join("\n"),
    );
    const map = parseDirectives(bytes, "python");
    expect(map.get(2)).toBe("off-file");
  });

  test("`on-file` directive captured at its line", () => {
    const bytes = buf(
      [
        "# autocontext: off-file",
        "y = 1",
        "# autocontext: on-file",
        "z = 2",
      ].join("\n"),
    );
    const map = parseDirectives(bytes, "python");
    expect(map.get(1)).toBe("off-file");
    expect(map.get(3)).toBe("on-file");
  });

  test("directive inside a triple-quoted string is NOT honored", () => {
    const bytes = buf(
      ['msg = """', "# autocontext: off", 'end"""', "client = x"].join("\n"),
    );
    const map = parseDirectives(bytes, "python");
    expect(map.get(3)).toBeUndefined();
  });
});

describe("parseDirectives — TypeScript/JavaScript", () => {
  test("`// autocontext: off` marks the next line", () => {
    const bytes = buf(
      ["const a = 1;", "// autocontext: off", "const b = 2;"].join("\n"),
    );
    const map = parseDirectives(bytes, "typescript");
    expect(map.get(3)).toBe("off");
  });

  test("`/* autocontext: off */` block-comment form honored", () => {
    const bytes = buf(
      ["const a = 1;", "/* autocontext: off */", "const b = 2;"].join("\n"),
    );
    const map = parseDirectives(bytes, "typescript");
    expect(map.get(3)).toBe("off");
  });

  test("directive inside a multi-line block comment NOT honored", () => {
    const bytes = buf(
      [
        "/* opens block",
        "// autocontext: off ← inside block comment",
        "still inside */",
        "const x = 1;",
      ].join("\n"),
    );
    const map = parseDirectives(bytes, "typescript");
    expect(map.get(3)).toBeUndefined();
    expect(map.get(4)).toBeUndefined();
  });
});

describe("P-directive-coverage — property (100 runs)", () => {
  test("a directive inserted at a random line always appears in the map", () => {
    fc.assert(
      fc.property(
        fc.array(fc.stringMatching(/^[a-z_][a-z0-9_]* = [0-9]+$/), { minLength: 1, maxLength: 8 }),
        fc.integer({ min: 0, max: 100 }),
        fc.constantFrom("off", "on", "off-file", "on-file"),
        (programLines, whichIdx, directive) => {
          if (programLines.length === 0) return true;
          const idx = whichIdx % programLines.length;
          const withDir = [...programLines];
          withDir.splice(idx, 0, `# autocontext: ${directive}`);
          const bytes = buf(withDir.join("\n"));
          const map = parseDirectives(bytes, "python");
          // For "off-file" / "on-file" the directive registers at its own line.
          // For "off" / "on" the directive registers at line N+1. In either
          // case, at least one entry should be present in the map.
          return map.size >= 1;
        },
      ),
      { numRuns: 100 },
    );
  });

  test("a directive embedded inside a single-line string never registers", () => {
    fc.assert(
      fc.property(
        fc.stringMatching(/^[a-z]{1,8}$/),
        fc.stringMatching(/^[a-z]{1,8}$/),
        fc.constantFrom("off", "on", "off-file", "on-file"),
        (prefix, suffix, directive) => {
          const bytes = buf(
            `x = "${prefix} # autocontext: ${directive} ${suffix}"\ny = 1\n`,
          );
          const map = parseDirectives(bytes, "python");
          return map.size === 0;
        },
      ),
      { numRuns: 100 },
    );
  });

  test("a directive inside a Python triple-quoted string never registers", () => {
    fc.assert(
      fc.property(
        fc.array(fc.stringMatching(/^[a-z ]{1,10}$/), { minLength: 1, maxLength: 3 }),
        fc.constantFrom("off", "on", "off-file", "on-file"),
        (innerLines, directive) => {
          const bytes = buf(
            [
              'msg = """',
              ...innerLines,
              `# autocontext: ${directive}`,
              '"""',
              "client = 1",
            ].join("\n"),
          );
          const map = parseDirectives(bytes, "python");
          return map.size === 0;
        },
      ),
      { numRuns: 100 },
    );
  });
});
