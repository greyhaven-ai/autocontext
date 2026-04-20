/**
 * A2-I Layer 5 — indentation-matcher unit tests (spec §6.3).
 *
 * Covers:
 *   - 2-space, 4-space, tab indentation
 *   - Nested scope (multi-level indent from predecessor line)
 *   - Single-line-indent (sparsely-indented) edge case — nearest-neighbor
 *     look-up resolves this even when file-level GCD under-detects width
 *   - Strip common leading whitespace from rawStatement before re-applying
 *   - Blank lines in rawStatement pass through
 */
import { describe, test, expect } from "vitest";
import { matchIndentation } from "../../../../src/control-plane/instrument/planner/indentation-matcher.js";
import { fromBytes } from "../../../../src/control-plane/instrument/scanner/source-file.js";
import type { SourceFile } from "../../../../src/control-plane/instrument/contract/index.js";

function pyFile(content: string): SourceFile {
  return fromBytes({
    path: "x.py",
    language: "python",
    bytes: Buffer.from(content, "utf-8"),
  });
}

function tsFile(content: string): SourceFile {
  return fromBytes({
    path: "x.ts",
    language: "typescript",
    bytes: Buffer.from(content, "utf-8"),
  });
}

describe("matchIndentation — top-level insertion", () => {
  test("top-level statement receives empty indent", () => {
    const sf = pyFile("import x\nprint(1)\n");
    const out = matchIndentation({
      sourceFile: sf,
      anchorLine: 2,
      rawStatement: "autocontext.init()",
    });
    expect(out).toBe("autocontext.init()");
  });

  test("first line insertion with blank predecessor falls back to empty", () => {
    const sf = pyFile("\nx = 1\n");
    const out = matchIndentation({
      sourceFile: sf,
      anchorLine: 1,
      rawStatement: "foo()",
    });
    expect(out).toBe("foo()");
  });
});

describe("matchIndentation — 4-space Python", () => {
  test("indent matches the previous non-blank line", () => {
    const sf = pyFile([
      "def f():",
      "    x = 1",
      "    y = 2",
      "",
    ].join("\n"));
    // Anchor line 4: previous non-blank is line 3 "    y = 2" (4-space indent).
    const out = matchIndentation({
      sourceFile: sf,
      anchorLine: 4,
      rawStatement: "z = 3",
    });
    expect(out).toBe("    z = 3");
  });

  test("nested scope (8-space) is detected from nearest predecessor", () => {
    const sf = pyFile([
      "def outer():",
      "    def inner():",
      "        a = 1",
      "        b = 2",
      "",
    ].join("\n"));
    // Anchor line 5: previous non-blank is line 4 "        b = 2" (8-space).
    const out = matchIndentation({
      sourceFile: sf,
      anchorLine: 5,
      rawStatement: "c = 3",
    });
    expect(out).toBe("        c = 3");
  });
});

describe("matchIndentation — 2-space TypeScript", () => {
  test("2-space indent preserved", () => {
    const sf = tsFile([
      "function f() {",
      "  const a = 1;",
      "}",
      "",
    ].join("\n"));
    // Anchor line 3 ("}"): previous non-blank is line 2 "  const a = 1;" (2-space).
    const out = matchIndentation({
      sourceFile: sf,
      anchorLine: 3,
      rawStatement: "const b = 2;",
    });
    expect(out).toBe("  const b = 2;");
  });
});

describe("matchIndentation — tabs", () => {
  test("tab indent preserved from predecessor", () => {
    const sf = pyFile([
      "def f():",
      "\tx = 1",
      "",
    ].join("\n"));
    // Anchor line 3: previous non-blank is line 2 "\tx = 1" (tab).
    const out = matchIndentation({
      sourceFile: sf,
      anchorLine: 3,
      rawStatement: "y = 2",
    });
    expect(out).toBe("\ty = 2");
  });
});

describe("matchIndentation — multi-line statements", () => {
  test("strips common leading whitespace from rawStatement", () => {
    const sf = pyFile([
      "def f():",
      "    pass",
      "",
    ].join("\n"));
    // Anchor line 3: previous non-blank is line 2 "    pass" (4-space).
    const raw = ["    with ctx():", "        run()"].join("\n");
    const out = matchIndentation({
      sourceFile: sf,
      anchorLine: 3,
      rawStatement: raw,
    });
    // Common strip of 4 spaces → re-apply enclosing (4 spaces from prior line):
    //   "with ctx():" → "    with ctx():"
    //   "    run()"    → "        run()"
    expect(out).toBe(["    with ctx():", "        run()"].join("\n"));
  });

  test("blank lines in rawStatement preserved as-is", () => {
    const sf = pyFile([
      "def f():",
      "    pass",
      "",
    ].join("\n"));
    const raw = ["a = 1", "", "b = 2"].join("\n");
    const out = matchIndentation({
      sourceFile: sf,
      anchorLine: 3,
      rawStatement: raw,
    });
    expect(out).toBe(["    a = 1", "", "    b = 2"].join("\n"));
  });
});

describe("matchIndentation — sparsely-indented edge case (Layer 1+2 concern)", () => {
  test("single-line-indent file: nearest-neighbor resolves ambiguity", () => {
    // File has one indented line at 3 spaces. GCD-based detection might clamp
    // this to 4 (the default). Nearest-neighbor look-up uses the ACTUAL 3-space
    // indent of the predecessor — more authoritative than file-level style.
    const sf = pyFile([
      "if cond:",
      "   x = 1",
      "",
    ].join("\n"));
    // Anchor line 3: previous non-blank is line 2 "   x = 1" (3-space).
    const out = matchIndentation({
      sourceFile: sf,
      anchorLine: 3,
      rawStatement: "y = 2",
    });
    expect(out).toBe("   y = 2");
  });
});
