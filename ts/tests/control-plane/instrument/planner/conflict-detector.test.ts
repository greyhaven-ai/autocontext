/**
 * A2-I Layer 5 — conflict-detector unit tests.
 *
 * Spec §6.4:
 *   - overlapping byte-ranges between two edits → conflict
 *   - InsertStatementEdit.anchor.range inside another edit's range → conflict
 *   - two WrapExpressionEdit with identical range + DIFFERENT wrapFn → conflict
 *   - two WrapExpressionEdit with identical range + IDENTICAL wrapFn → deduplicate (silent)
 *   - same-plugin conflicts still report kind:"conflict" so CLI can exit 13
 */
import { describe, test, expect } from "vitest";
import { detectConflicts } from "../../../../src/control-plane/instrument/planner/conflict-detector.js";
import type {
  EditDescriptor,
  SourceRange,
  WrapExpressionEdit,
  InsertStatementEdit,
} from "../../../../src/control-plane/instrument/contract/index.js";

function rangeOf(startByte: number, endByte: number): SourceRange {
  return {
    startByte,
    endByte,
    startLineCol: { line: 1, col: startByte },
    endLineCol: { line: 1, col: endByte },
  };
}

function wrapEdit(opts: {
  pluginId?: string;
  range: SourceRange;
  wrapFn?: string;
}): WrapExpressionEdit {
  return {
    kind: "wrap-expression",
    pluginId: opts.pluginId ?? "plugin-a",
    sourceFilePath: "src/main.py",
    importsNeeded: [],
    range: opts.range,
    wrapFn: opts.wrapFn ?? "instrument_client",
  };
}

function insertEdit(opts: {
  pluginId?: string;
  anchorRange: SourceRange;
  anchorKind?: "before" | "after";
  statementSource?: string;
}): InsertStatementEdit {
  return {
    kind: "insert-statement",
    pluginId: opts.pluginId ?? "plugin-b",
    sourceFilePath: "src/main.py",
    importsNeeded: [],
    anchor: { kind: opts.anchorKind ?? "before", range: opts.anchorRange },
    statementSource: opts.statementSource ?? "autocontext.init()",
  };
}

describe("detectConflicts — non-overlapping", () => {
  test("two disjoint wrap edits return ok with both edits preserved", () => {
    const edits: EditDescriptor[] = [
      wrapEdit({ range: rangeOf(0, 5) }),
      wrapEdit({ range: rangeOf(10, 20) }),
    ];
    const report = detectConflicts(edits);
    expect(report.kind).toBe("ok");
    if (report.kind === "ok") {
      expect(report.deduplicatedEdits).toHaveLength(2);
    }
  });

  test("empty input returns ok with empty array", () => {
    const report = detectConflicts([]);
    expect(report.kind).toBe("ok");
    if (report.kind === "ok") {
      expect(report.deduplicatedEdits).toHaveLength(0);
    }
  });

  test("touching ranges (end == start) are NOT overlapping", () => {
    // Half-open convention: [5, 10) and [10, 15) do not overlap.
    const edits: EditDescriptor[] = [
      wrapEdit({ range: rangeOf(5, 10) }),
      wrapEdit({ range: rangeOf(10, 15) }),
    ];
    const report = detectConflicts(edits);
    expect(report.kind).toBe("ok");
  });
});

describe("detectConflicts — overlapping ranges", () => {
  test("partially overlapping wrap edits returns conflict", () => {
    const edits: EditDescriptor[] = [
      wrapEdit({ pluginId: "a", range: rangeOf(0, 10) }),
      wrapEdit({ pluginId: "b", range: rangeOf(5, 15) }),
    ];
    const report = detectConflicts(edits);
    expect(report.kind).toBe("conflict");
    if (report.kind === "conflict") {
      expect(report.reason.kind).toBe("overlapping-ranges");
    }
  });

  test("fully-contained ranges (replace inside wrap) returns conflict", () => {
    const edits: EditDescriptor[] = [
      wrapEdit({ range: rangeOf(0, 20) }),
      {
        kind: "replace-expression",
        pluginId: "c",
        sourceFilePath: "src/main.py",
        importsNeeded: [],
        range: rangeOf(5, 10),
        replacementSource: "new",
      },
    ];
    const report = detectConflicts(edits);
    expect(report.kind).toBe("conflict");
    if (report.kind === "conflict") {
      expect(report.reason.kind).toBe("overlapping-ranges");
    }
  });
});

describe("detectConflicts — insert-anchor-inside-edit", () => {
  test("insert-statement with anchor range inside wrap range is conflict", () => {
    const wrap = wrapEdit({ range: rangeOf(0, 20) });
    const ins = insertEdit({ anchorRange: rangeOf(8, 12) });
    const report = detectConflicts([wrap, ins]);
    expect(report.kind).toBe("conflict");
    if (report.kind === "conflict") {
      expect(report.reason.kind).toBe("insert-anchor-inside-another-edit");
    }
  });

  test("insert-statement whose anchor is AT boundary is not conflict", () => {
    // Half-open: wrap covers [0,20); anchor at [20,25) is disjoint.
    const wrap = wrapEdit({ range: rangeOf(0, 20) });
    const ins = insertEdit({ anchorRange: rangeOf(20, 25) });
    const report = detectConflicts([wrap, ins]);
    expect(report.kind).toBe("ok");
  });

  test("two insert-statements at the SAME anchor range do NOT conflict", () => {
    const ins1 = insertEdit({ anchorRange: rangeOf(10, 15), statementSource: "a()" });
    const ins2 = insertEdit({ anchorRange: rangeOf(10, 15), statementSource: "b()" });
    const report = detectConflicts([ins1, ins2]);
    // Equal anchor ranges are allowed — they're both insertions at the same spot,
    // applied in order. No conflict per §6.4 (which specifies "anchor INSIDE another EDIT's range").
    expect(report.kind).toBe("ok");
  });
});

describe("detectConflicts — same-range wrap dedup vs conflict", () => {
  test("same-range + SAME wrapFn deduplicates to one edit (silent)", () => {
    const e1 = wrapEdit({ pluginId: "a", range: rangeOf(0, 10), wrapFn: "instrument_client" });
    const e2 = wrapEdit({ pluginId: "a", range: rangeOf(0, 10), wrapFn: "instrument_client" });
    const report = detectConflicts([e1, e2]);
    expect(report.kind).toBe("ok");
    if (report.kind === "ok") {
      expect(report.deduplicatedEdits).toHaveLength(1);
    }
  });

  test("same-range + DIFFERENT wrapFn returns conflict", () => {
    const e1 = wrapEdit({ pluginId: "a", range: rangeOf(0, 10), wrapFn: "instrument_client" });
    const e2 = wrapEdit({ pluginId: "b", range: rangeOf(0, 10), wrapFn: "wrap_openai" });
    const report = detectConflicts([e1, e2]);
    expect(report.kind).toBe("conflict");
    if (report.kind === "conflict") {
      expect(report.reason.kind).toBe("same-range-different-wrapfn");
    }
  });

  test("same-range wrap with same wrapFn but different plugin still dedupes", () => {
    // Duplicate detection is value-based: identical range + identical wrapFn, regardless of
    // which plugin produced it. Spec §6.4: "deduplicated silently; logged at DEBUG".
    const e1 = wrapEdit({ pluginId: "a", range: rangeOf(0, 10), wrapFn: "instrument_client" });
    const e2 = wrapEdit({ pluginId: "b", range: rangeOf(0, 10), wrapFn: "instrument_client" });
    const report = detectConflicts([e1, e2]);
    expect(report.kind).toBe("ok");
    if (report.kind === "ok") {
      expect(report.deduplicatedEdits).toHaveLength(1);
    }
  });
});

describe("detectConflicts — dedup preserves insertion order", () => {
  test("first occurrence wins on dedup", () => {
    const e1 = wrapEdit({ pluginId: "first", range: rangeOf(0, 10), wrapFn: "f" });
    const e2 = wrapEdit({ pluginId: "second", range: rangeOf(0, 10), wrapFn: "f" });
    const e3 = wrapEdit({ range: rangeOf(20, 30) });
    const report = detectConflicts([e1, e2, e3]);
    expect(report.kind).toBe("ok");
    if (report.kind === "ok") {
      expect(report.deduplicatedEdits).toHaveLength(2);
      expect(report.deduplicatedEdits[0]!.pluginId).toBe("first");
    }
  });
});
