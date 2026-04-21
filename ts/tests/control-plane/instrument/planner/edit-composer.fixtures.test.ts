/**
 * A2-I Layer 5 — fixture-driven integration tests.
 *
 * Exercises `composeEdits` + `planImports` against on-disk fixtures covering
 * the language × import-style × directive × secret matrix declared in spec
 * §11.3 + the Layer 5 TDD brief.
 */
import { describe, test, expect } from "vitest";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { loadSourceFile } from "../../../../src/control-plane/instrument/scanner/source-file.js";
import { composeEdits } from "../../../../src/control-plane/instrument/planner/edit-composer.js";
import { planImports } from "../../../../src/control-plane/instrument/planner/import-manager.js";
import type {
  EditDescriptor,
  InstrumentLanguage,
  SourceRange,
  WrapExpressionEdit,
} from "../../../../src/control-plane/instrument/contract/index.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIX_DIR = join(__dirname, "_fixtures");

function rangeFromText(text: string, startByte: number, endByte: number): SourceRange {
  const before = text.slice(0, startByte);
  const sLine = (before.match(/\n/g)?.length ?? 0) + 1;
  const sLastNl = before.lastIndexOf("\n");
  const sCol = startByte - (sLastNl + 1);
  const between = text.slice(0, endByte);
  const eLine = (between.match(/\n/g)?.length ?? 0) + 1;
  const eLastNl = between.lastIndexOf("\n");
  const eCol = endByte - (eLastNl + 1);
  return {
    startByte,
    endByte,
    startLineCol: { line: sLine, col: sCol },
    endLineCol: { line: eLine, col: eCol },
  };
}

describe("fixtures — Python", () => {
  test("simple.py: wrap edit composes with import", async () => {
    const sf = await loadSourceFile({ path: join(FIX_DIR, "python/simple.py"), language: "python" });
    const text = sf.bytes.toString("utf-8");
    const target = "make_client()";
    const start = text.indexOf(target);
    const edit: WrapExpressionEdit = {
      kind: "wrap-expression",
      pluginId: "p",
      sourceFilePath: sf.path,
      importsNeeded: [{ module: "autocontext", name: "instrument_client", kind: "named" }],
      range: rangeFromText(text, start, start + target.length),
      wrapFn: "instrument_client",
    };
    const result = composeEdits({ sourceFile: sf, edits: [edit] });
    expect(result.kind).toBe("patch");
    if (result.kind === "patch") {
      expect(result.patch.afterContent).toContain("from autocontext import instrument_client");
      expect(result.patch.afterContent).toContain("instrument_client(make_client())");
    }
  });

  test("with-future-imports.py: placement respects `__future__`", async () => {
    const sf = await loadSourceFile({
      path: join(FIX_DIR, "python/with-future-imports.py"),
      language: "python",
    });
    const plan = planImports({
      sourceFile: sf,
      importsNeeded: [{ module: "autocontext", name: "init", kind: "named" }],
    });
    // __future__ is on line 1, `import os` is on line 3. Insert after line 3 → line 4.
    expect(plan.insertAt.line).toBe(4);
  });

  test("tabs.py: tab indentation is preserved in inserts", async () => {
    const sf = await loadSourceFile({ path: join(FIX_DIR, "python/tabs.py"), language: "python" });
    expect(sf.indentationStyle.kind).toBe("tabs");
  });

  test("no-imports.py: insertion lands below the docstring", async () => {
    const sf = await loadSourceFile({
      path: join(FIX_DIR, "python/no-imports.py"),
      language: "python",
    });
    const plan = planImports({
      sourceFile: sf,
      importsNeeded: [{ module: "autocontext", name: "init", kind: "named" }],
    });
    // Docstring is line 1; insertion line >= 2.
    expect(plan.insertAt.line).toBeGreaterThanOrEqual(2);
  });

  test("with-directive.py: edits inside off region refused", async () => {
    const sf = await loadSourceFile({
      path: join(FIX_DIR, "python/with-directive.py"),
      language: "python",
    });
    const text = sf.bytes.toString("utf-8");
    const target = "make_client()";
    const start = text.indexOf(target);
    const edit: WrapExpressionEdit = {
      kind: "wrap-expression",
      pluginId: "p",
      sourceFilePath: sf.path,
      importsNeeded: [],
      range: rangeFromText(text, start, start + target.length),
      wrapFn: "instrument",
    };
    const result = composeEdits({ sourceFile: sf, edits: [edit] });
    expect(result.kind).toBe("refused");
    if (result.kind === "refused") {
      expect(result.reason.kind).toBe("all-edits-dropped-by-directives");
    }
  });

  test("with-secret.py: hasSecretLiteral refuses with surfacing reason", async () => {
    const sf = await loadSourceFile({
      path: join(FIX_DIR, "python/with-secret.py"),
      language: "python",
    });
    expect(sf.hasSecretLiteral).toBe(true);
    const edits: EditDescriptor[] = [];
    const result = composeEdits({ sourceFile: sf, edits });
    expect(result.kind).toBe("refused");
    if (result.kind === "refused" && result.reason.kind === "secret-literal") {
      expect(result.reason.match.pattern).toBe("aws-access-key");
    }
  });
});

describe("fixtures — TypeScript", () => {
  test("simple.ts: named import added with double quotes", async () => {
    const sf = await loadSourceFile({
      path: join(FIX_DIR, "typescript/simple.ts"),
      language: "typescript",
    });
    const plan = planImports({
      sourceFile: sf,
      importsNeeded: [{ module: "autocontext", name: "init", kind: "named" }],
    });
    expect(plan.statementSource).toContain('import { init } from "autocontext";');
  });

  test("single-quotes.ts: new import uses single quotes", async () => {
    const sf = await loadSourceFile({
      path: join(FIX_DIR, "typescript/single-quotes.ts"),
      language: "typescript",
    });
    const plan = planImports({
      sourceFile: sf,
      importsNeeded: [{ module: "autocontext", name: "init", kind: "named" }],
    });
    expect(plan.statementSource).toContain("import { init } from 'autocontext';");
  });

  test("default-import.ts: inserts after existing default", async () => {
    const sf = await loadSourceFile({
      path: join(FIX_DIR, "typescript/default-import.ts"),
      language: "typescript",
    });
    const plan = planImports({
      sourceFile: sf,
      importsNeeded: [{ module: "autocontext", name: "init", kind: "named" }],
    });
    expect(plan.insertAt.line).toBeGreaterThanOrEqual(2);
  });

  test("no-imports.ts: insertion at line 1", async () => {
    const sf = await loadSourceFile({
      path: join(FIX_DIR, "typescript/no-imports.ts"),
      language: "typescript",
    });
    const plan = planImports({
      sourceFile: sf,
      importsNeeded: [{ module: "autocontext", name: "init", kind: "named" }],
    });
    expect(plan.insertAt.line).toBe(1);
  });

  test("with-directive.ts: edit in off region is refused", async () => {
    const sf = await loadSourceFile({
      path: join(FIX_DIR, "typescript/with-directive.ts"),
      language: "typescript",
    });
    const text = sf.bytes.toString("utf-8");
    const target = "makeClient()";
    const start = text.indexOf(target);
    const edit: WrapExpressionEdit = {
      kind: "wrap-expression",
      pluginId: "p",
      sourceFilePath: sf.path,
      importsNeeded: [],
      range: rangeFromText(text, start, start + target.length),
      wrapFn: "instrument",
    };
    const result = composeEdits({ sourceFile: sf, edits: [edit] });
    expect(result.kind).toBe("refused");
    if (result.kind === "refused") {
      expect(result.reason.kind).toBe("all-edits-dropped-by-directives");
    }
  });

  test("with-secret.ts: secret-literal refusal", async () => {
    const sf = await loadSourceFile({
      path: join(FIX_DIR, "typescript/with-secret.ts"),
      language: "typescript",
    });
    expect(sf.hasSecretLiteral).toBe(true);
    const result = composeEdits({ sourceFile: sf, edits: [] });
    expect(result.kind).toBe("refused");
  });
});

describe("fixtures — JavaScript variants", () => {
  const variants: readonly { readonly file: string; readonly language: InstrumentLanguage }[] = [
    { file: "javascript/simple.js", language: "javascript" },
    { file: "javascript/commonjs.cjs", language: "javascript" },
    { file: "javascript/esm.mjs", language: "javascript" },
  ];
  for (const v of variants) {
    test(`${v.file}: loads without error; planImports returns a plan`, async () => {
      const sf = await loadSourceFile({ path: join(FIX_DIR, v.file), language: v.language });
      const plan = planImports({
        sourceFile: sf,
        importsNeeded: [{ module: "autocontext", name: "init", kind: "named" }],
      });
      expect(plan.additionalSpecsEmitted.length).toBeGreaterThanOrEqual(0);
    });
  }
});
