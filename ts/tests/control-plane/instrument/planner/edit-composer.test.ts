/**
 * A2-I Layer 5 — edit-composer unit tests (spec §6.1).
 *
 * Full-flow with fixture SourceFiles + mock edits:
 *   - safety refusal (hasSecretLiteral)
 *   - conflict propagation
 *   - directive-filtered edits
 *   - import-manager contribution accumulation
 *   - right-to-left application
 */
import { describe, test, expect } from "vitest";
import { composeEdits } from "../../../../src/control-plane/instrument/planner/edit-composer.js";
import { fromBytes } from "../../../../src/control-plane/instrument/scanner/source-file.js";
import type {
  EditDescriptor,
  InsertStatementEdit,
  SourceFile,
  SourceRange,
  WrapExpressionEdit,
} from "../../../../src/control-plane/instrument/contract/index.js";

/** Construct a SourceRange from start/end bytes in `text`. Fills line/col from \n count. */
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

function pyFile(content: string): SourceFile {
  return fromBytes({ path: "src/main.py", language: "python", bytes: Buffer.from(content, "utf-8") });
}

describe("composeEdits — safety refusal", () => {
  test("hasSecretLiteral refuses with reason.kind='secret-literal' and surfaces match", () => {
    const content = ["import os", "", 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"', ""].join("\n");
    const sf = pyFile(content);
    expect(sf.hasSecretLiteral).toBe(true);
    const edit: WrapExpressionEdit = {
      kind: "wrap-expression",
      pluginId: "p",
      sourceFilePath: "src/main.py",
      importsNeeded: [],
      range: rangeFromText(content, 0, 5),
      wrapFn: "w",
    };
    const result = composeEdits({ sourceFile: sf, edits: [edit] });
    expect(result.kind).toBe("refused");
    if (result.kind === "refused" && result.reason.kind === "secret-literal") {
      expect(result.reason.match.pattern).toBe("aws-access-key");
      expect(result.reason.match.lineNumber).toBe(3);
      // Error message cites pattern + line number (spec §5.4 template).
      expect(result.reason.message).toContain("line 3");
      expect(result.reason.message.toLowerCase()).toContain("aws access key");
    }
  });
});

describe("composeEdits — conflict propagation", () => {
  test("overlapping edits propagate conflict", () => {
    const content = "abcdefghij\n";
    const sf = pyFile(content);
    const edits: EditDescriptor[] = [
      {
        kind: "wrap-expression",
        pluginId: "a",
        sourceFilePath: "src/main.py",
        importsNeeded: [],
        range: rangeFromText(content, 0, 5),
        wrapFn: "f",
      },
      {
        kind: "wrap-expression",
        pluginId: "b",
        sourceFilePath: "src/main.py",
        importsNeeded: [],
        range: rangeFromText(content, 3, 8),
        wrapFn: "g",
      },
    ];
    const result = composeEdits({ sourceFile: sf, edits });
    expect(result.kind).toBe("conflict");
    if (result.kind === "conflict") {
      expect(result.reason.kind).toBe("overlapping-ranges");
    }
  });
});

describe("composeEdits — directive filter", () => {
  test("edit inside `# autocontext: off` region is dropped", () => {
    // Line 3 is "client = ..."; "off" on line 2 applies to line 3.
    const content = [
      "import os",               // 1
      "# autocontext: off",      // 2
      "client = make_client()",  // 3 — off
      "",
    ].join("\n");
    const sf = pyFile(content);
    const target = content.indexOf("make_client()");
    const edit: WrapExpressionEdit = {
      kind: "wrap-expression",
      pluginId: "p",
      sourceFilePath: "src/main.py",
      importsNeeded: [],
      range: rangeFromText(content, target, target + "make_client()".length),
      wrapFn: "instrument",
    };
    const result = composeEdits({ sourceFile: sf, edits: [edit] });
    // All edits dropped by directive → refused with that reason.
    expect(result.kind).toBe("refused");
    if (result.kind === "refused") {
      expect(result.reason.kind).toBe("all-edits-dropped-by-directives");
    }
  });

  test("one edit in off-region, one outside: outside survives, patch returned", () => {
    // Line 4 is "other()"; "off" on line 3 applies to line 4.
    // Line 6 is "kept()"; no directive.
    const content = [
      "import os",               // 1
      "kept()",                  // 2 — NOT off
      "# autocontext: off",      // 3
      "other()",                 // 4 — off
      "# autocontext: on",       // 5
      "kept_too()",              // 6 — on
      "",
    ].join("\n");
    const sf = pyFile(content);
    const keptStart = content.indexOf("kept()");
    const otherStart = content.indexOf("other()");

    const keptEdit: WrapExpressionEdit = {
      kind: "wrap-expression",
      pluginId: "p",
      sourceFilePath: "src/main.py",
      importsNeeded: [],
      range: rangeFromText(content, keptStart, keptStart + "kept()".length),
      wrapFn: "instrument",
    };
    const offEdit: WrapExpressionEdit = {
      kind: "wrap-expression",
      pluginId: "p",
      sourceFilePath: "src/main.py",
      importsNeeded: [],
      range: rangeFromText(content, otherStart, otherStart + "other()".length),
      wrapFn: "instrument",
    };
    const result = composeEdits({ sourceFile: sf, edits: [keptEdit, offEdit] });
    expect(result.kind).toBe("patch");
    if (result.kind === "patch") {
      // The kept edit survived; the off edit did not.
      expect(result.patch.afterContent).toContain("instrument(kept())");
      expect(result.patch.afterContent).toContain("other()"); // still unmodified
      expect(result.patch.afterContent).not.toContain("instrument(other())");
    }
  });
});

describe("composeEdits — import accumulation + indentation + patch", () => {
  test("wrap edit with importsNeeded yields patch with import block", () => {
    const content = "import os\n\nclient = make_client()\n";
    const sf = pyFile(content);
    const start = content.indexOf("make_client()");
    const edit: WrapExpressionEdit = {
      kind: "wrap-expression",
      pluginId: "p",
      sourceFilePath: "src/main.py",
      importsNeeded: [{ module: "autocontext", name: "instrument_client", kind: "named" }],
      range: rangeFromText(content, start, start + "make_client()".length),
      wrapFn: "instrument_client",
    };
    const result = composeEdits({ sourceFile: sf, edits: [edit] });
    expect(result.kind).toBe("patch");
    if (result.kind === "patch") {
      expect(result.patch.afterContent).toContain(
        "from autocontext import instrument_client",
      );
      expect(result.patch.afterContent).toContain(
        "instrument_client(make_client())",
      );
    }
  });

  test("insert-statement edit re-indents to enclosing scope", () => {
    const content = ["def f():", "    x = 1", ""].join("\n");
    const sf = pyFile(content);
    const xStart = content.indexOf("x = 1");
    const xEnd = xStart + "x = 1".length;
    const edit: InsertStatementEdit = {
      kind: "insert-statement",
      pluginId: "p",
      sourceFilePath: "src/main.py",
      importsNeeded: [],
      anchor: { kind: "after", range: rangeFromText(content, xStart, xEnd) },
      statementSource: "y = 2",
    };
    const result = composeEdits({ sourceFile: sf, edits: [edit] });
    expect(result.kind).toBe("patch");
    if (result.kind === "patch") {
      // The inserted "y = 2" must be indented to match "x = 1" (4 spaces).
      expect(result.patch.afterContent).toContain("    y = 2");
    }
  });
});

describe("composeEdits — right-to-left application", () => {
  test("two non-conflicting wrap edits apply independently", () => {
    const content = "aaa bbb\n";
    const sf = pyFile(content);
    const edits: WrapExpressionEdit[] = [
      {
        kind: "wrap-expression",
        pluginId: "a",
        sourceFilePath: "src/main.py",
        importsNeeded: [],
        range: rangeFromText(content, 0, 3),
        wrapFn: "f",
      },
      {
        kind: "wrap-expression",
        pluginId: "b",
        sourceFilePath: "src/main.py",
        importsNeeded: [],
        range: rangeFromText(content, 4, 7),
        wrapFn: "g",
      },
    ];
    const result = composeEdits({ sourceFile: sf, edits });
    expect(result.kind).toBe("patch");
    if (result.kind === "patch") {
      expect(result.patch.afterContent).toContain("f(aaa)");
      expect(result.patch.afterContent).toContain("g(bbb)");
    }
  });
});

describe("composeEdits — empty edits", () => {
  test("no edits → patch with no changes", () => {
    const content = "x = 1\n";
    const sf = pyFile(content);
    const result = composeEdits({ sourceFile: sf, edits: [] });
    // When no edits, importPlan is empty and result is an unchanged patch.
    expect(result.kind).toBe("patch");
    if (result.kind === "patch") {
      expect(result.patch.afterContent).toBe(content);
    }
  });
});
