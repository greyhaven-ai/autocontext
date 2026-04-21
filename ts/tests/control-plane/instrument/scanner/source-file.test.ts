/**
 * A2-I Layer 2 — SourceFile wrapper + directive parsing + existingImports +
 * indentation detection + lazy tree access.
 */
import { describe, test, expect } from "vitest";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";
import {
  fromBytes,
  loadSourceFile,
  parseDirectives,
  parseExistingImports,
  detectIndentationStyle,
} from "../../../../src/control-plane/instrument/scanner/source-file.js";
import fc from "fast-check";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURES = join(__dirname, "..", "_fixtures", "scanner");

describe("parseDirectives — Python", () => {
  test("`# autocontext: off` at line N marks line N+1", () => {
    const lines = [
      "import x",      // line 1
      "# autocontext: off", // line 2
      "y = 1",          // line 3 (expected off)
    ];
    const map = parseDirectives(lines, "python");
    expect(map.get(3)).toBe("off");
    expect(map.size).toBe(1);
  });

  test("`off-file` applies from that line onward", () => {
    const lines = [
      "import x",            // 1
      "# autocontext: off-file", // 2 — applies from here
      "y = 1",               // 3 (off-file still in effect)
    ];
    const map = parseDirectives(lines, "python");
    expect(map.get(2)).toBe("off-file");
  });

  test("`on-file` directive captured at its line", () => {
    const lines = [
      "# autocontext: off-file",
      "y = 1",
      "# autocontext: on-file",
      "z = 2",
    ];
    const map = parseDirectives(lines, "python");
    expect(map.get(1)).toBe("off-file");
    expect(map.get(3)).toBe("on-file");
  });

  test("directive inside a triple-quoted string is NOT honored", () => {
    const lines = [
      'msg = """',
      "# autocontext: off",  // inside the string literal
      'end"""',
      "client = x",
    ];
    const map = parseDirectives(lines, "python");
    // The # autocontext: off inside the docstring must NOT register. Next-line
    // scope would mean line 3 is off; we assert line 3 is NOT off.
    expect(map.get(3)).toBeUndefined();
  });

  test("non-directive comments ignored", () => {
    const lines = [
      "# just a normal comment",
      "x = 1",
      "# TODO: something",
      "y = 2",
    ];
    const map = parseDirectives(lines, "python");
    expect(map.size).toBe(0);
  });
});

describe("parseDirectives — TypeScript/JavaScript", () => {
  test("`// autocontext: off` marks the next line", () => {
    const lines = [
      "const a = 1;",
      "// autocontext: off",
      "const b = 2;",
    ];
    const map = parseDirectives(lines, "typescript");
    expect(map.get(3)).toBe("off");
  });

  test("`/* autocontext: off */` block-comment form is honored", () => {
    const lines = [
      "const a = 1;",
      "/* autocontext: off */",
      "const b = 2;",
    ];
    const map = parseDirectives(lines, "typescript");
    expect(map.get(3)).toBe("off");
  });

  test("JSX directive parsed", () => {
    const lines = [
      "// autocontext: off",
      "<div />",
    ];
    const map = parseDirectives(lines, "jsx");
    expect(map.get(2)).toBe("off");
  });
});

describe("parseDirectives — property test (P-directive-parser)", () => {
  test("directives embedded in arbitrary surrounding text never register inside a string", () => {
    fc.assert(
      fc.property(
        fc.stringMatching(/^[a-z]{1,8}$/),
        fc.stringMatching(/^[a-z]{1,8}$/),
        (prefix, suffix) => {
          // The string contains the literal directive text, but the whole thing
          // is a regular Python string assignment. Directive must NOT register
          // (regex is anchored at start-of-line after whitespace only).
          const lines = [`x = "${prefix} # autocontext: off ${suffix}"`, "y = 1"];
          const map = parseDirectives(lines, "python");
          return map.size === 0;
        },
      ),
      { numRuns: 100 },
    );
  });

  test("directive lines with a leading Python string literal do not trigger (embedded in triple)", () => {
    fc.assert(
      fc.property(fc.boolean(), (_b) => {
        const lines = [
          'msg = """',
          "# autocontext: off",
          '"""',
          "client = 1",
        ];
        const map = parseDirectives(lines, "python");
        return map.get(3) === undefined && map.get(4) === undefined;
      }),
      { numRuns: 100 },
    );
  });
});

/** Helper: find an ImportedName entry by its source name in a Set<ImportedName>. */
function hasName(names: ReadonlySet<import("../../../../src/control-plane/instrument/contract/plugin-interface.js").ImportedName>, name: string): boolean {
  for (const n of names) if (n.name === name) return true;
  return false;
}

describe("parseExistingImports — Python", () => {
  test("from-import captured with module and name", () => {
    const lines = ["from openai import OpenAI, AsyncOpenAI", "import os"];
    const set = parseExistingImports(lines, "python");
    const byModule = new Map(Array.from(set).map((i) => [i.module, i.names]));
    expect(byModule.get("openai")).toBeDefined();
    expect(hasName(byModule.get("openai")!, "OpenAI")).toBe(true);
    expect(hasName(byModule.get("openai")!, "AsyncOpenAI")).toBe(true);
    expect(byModule.get("os")).toBeDefined();
  });

  test("`from openai import OpenAI as Client` captures `OpenAI` as name with alias `Client`", () => {
    const lines = ["from openai import OpenAI as Client"];
    const set = parseExistingImports(lines, "python");
    const byModule = new Map(Array.from(set).map((i) => [i.module, i.names]));
    expect(hasName(byModule.get("openai")!, "OpenAI")).toBe(true);
    // alias is preserved
    const entry = Array.from(byModule.get("openai")!).find((n) => n.name === "OpenAI");
    expect(entry?.alias).toBe("Client");
  });
});

describe("parseExistingImports — TypeScript/JavaScript", () => {
  test("named imports captured", () => {
    const lines = [`import { Anthropic, Tool } from "@anthropic-ai/sdk";`];
    const set = parseExistingImports(lines, "typescript");
    const byModule = new Map(Array.from(set).map((i) => [i.module, i.names]));
    expect(hasName(byModule.get("@anthropic-ai/sdk")!, "Anthropic")).toBe(true);
    expect(hasName(byModule.get("@anthropic-ai/sdk")!, "Tool")).toBe(true);
  });

  test("default import captured (name='default', alias=binding)", () => {
    const lines = [`import OpenAI from "openai";`];
    const set = parseExistingImports(lines, "typescript");
    const byModule = new Map(Array.from(set).map((i) => [i.module, i.names]));
    // default import: name="default", alias="OpenAI"
    const entry = Array.from(byModule.get("openai")!).find((n) => n.name === "default");
    expect(entry).toBeDefined();
    expect(entry?.alias).toBe("OpenAI");
  });

  test("namespace import captured (name=mod, alias=binding)", () => {
    const lines = [`import * as ts from "typescript";`];
    const set = parseExistingImports(lines, "typescript");
    const byModule = new Map(Array.from(set).map((i) => [i.module, i.names]));
    // namespace import: name="typescript", alias="ts"
    const entry = Array.from(byModule.get("typescript")!).find((n) => n.alias === "ts");
    expect(entry).toBeDefined();
    expect(entry?.name).toBe("typescript");
  });

  test("side-effect import recorded with empty names", () => {
    const lines = [`import "polyfill";`];
    const set = parseExistingImports(lines, "typescript");
    const byModule = new Map(Array.from(set).map((i) => [i.module, i.names]));
    expect(byModule.has("polyfill")).toBe(true);
    expect(byModule.get("polyfill")!.size).toBe(0);
  });
});

describe("detectIndentationStyle", () => {
  test("tabs detected", () => {
    const lines = ["def f():", "\tx = 1", "\tif x:", "\t\ty = 2"];
    expect(detectIndentationStyle(lines)).toEqual({ kind: "tabs" });
  });

  test("4-space indentation detected", () => {
    const lines = ["def f():", "    x = 1", "    if x:", "        y = 2"];
    expect(detectIndentationStyle(lines)).toEqual({ kind: "spaces", width: 4 });
  });

  test("2-space indentation detected", () => {
    const lines = ["function f() {", "  if (x) {", "    y();", "  }", "}"];
    expect(detectIndentationStyle(lines)).toEqual({ kind: "spaces", width: 2 });
  });

  test("empty file defaults to 4-space", () => {
    expect(detectIndentationStyle([])).toEqual({ kind: "spaces", width: 4 });
  });
});

describe("SourceFile — lazy tree access", () => {
  test("`.tree` is not parsed until first read", async () => {
    const bytes = Buffer.from("x = 1\n", "utf-8");
    const sf = fromBytes({ path: "test.py", language: "python", bytes });
    // Reading the getter returns a Promise. Not reading it should skip parsing.
    // We can't observe "not-parsed" from outside without mocking; instead we
    // verify that repeated access returns the SAME cached promise.
    const p1 = sf.tree as Promise<unknown>;
    const p2 = sf.tree as Promise<unknown>;
    expect(p1).toBe(p2);
    // Awaiting it yields a tree.
    const tree = await p1;
    expect(tree).toBeDefined();
  });

  test("loadSourceFile from disk reads bytes + builds directives + existingImports", async () => {
    const path = join(FIXTURES, "simple-repo", "src", "app.py");
    const sf = await loadSourceFile({ path, language: "python" });
    expect(sf.language).toBe("python");
    expect(sf.bytes.length).toBeGreaterThan(0);
    const mods = Array.from(sf.existingImports).map((i) => i.module);
    expect(mods).toContain("openai");
    expect(mods).toContain("os");
    expect(sf.hasSecretLiteral).toBe(false);
    // No directives in app.py
    expect(sf.directives.size).toBe(0);
  });
});
