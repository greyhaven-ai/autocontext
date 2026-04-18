import { describe, test, expect } from "vitest";
import { applyPatch } from "diff";
import fc from "fast-check";
import { emitUnifiedDiff } from "../../../../src/control-plane/actuators/_shared/unified-diff-emitter.js";
import { validatePatch } from "../../../../src/control-plane/contract/validators.js";

describe("emitUnifiedDiff", () => {
  test("create: oldContent empty + newContent non-empty → operation=create, valid Patch", () => {
    const patch = emitUnifiedDiff({
      filePath: "agents/grid_ctf/prompts/p.txt",
      oldContent: "",
      newContent: "hello\nworld\n",
    });
    expect(patch.operation).toBe("create");
    expect(patch.filePath).toBe("agents/grid_ctf/prompts/p.txt");
    expect(patch.afterContent).toBe("hello\nworld\n");
    expect(patch.unifiedDiff).toMatch(/@@/);
    expect(validatePatch(patch).valid).toBe(true);
  });

  test("modify: both non-empty and different → operation=modify", () => {
    const patch = emitUnifiedDiff({
      filePath: "agents/grid_ctf/prompts/p.txt",
      oldContent: "a\nb\n",
      newContent: "a\nB\n",
    });
    expect(patch.operation).toBe("modify");
    expect(patch.afterContent).toBe("a\nB\n");
  });

  test("delete: newContent empty + oldContent non-empty → operation=delete", () => {
    const patch = emitUnifiedDiff({
      filePath: "agents/grid_ctf/prompts/p.txt",
      oldContent: "gone\n",
      newContent: "",
    });
    expect(patch.operation).toBe("delete");
  });

  test("roundtrip: applying the unified diff to oldContent yields newContent", () => {
    const old = "line 1\nline 2\nline 3\n";
    const nu = "line 1\nline TWO\nline 3\nline 4\n";
    const patch = emitUnifiedDiff({
      filePath: "x.txt",
      oldContent: old,
      newContent: nu,
    });
    const applied = applyPatch(old, patch.unifiedDiff);
    expect(applied).toBe(nu);
  });

  test("no-op (oldContent === newContent): emits a patch whose diff body is empty-ish and roundtrips to the same content", () => {
    const content = "unchanged\n";
    const patch = emitUnifiedDiff({
      filePath: "x.txt",
      oldContent: content,
      newContent: content,
    });
    expect(patch.operation).toBe("modify");
    // Applying an empty patch body must return the original.
    const applied = applyPatch(content, patch.unifiedDiff);
    expect(applied).toBe(content);
  });

  test("property: round-trip holds for arbitrary line-based contents", () => {
    fc.assert(
      fc.property(
        fc.array(fc.string({ minLength: 0, maxLength: 16 }), { minLength: 0, maxLength: 6 }),
        fc.array(fc.string({ minLength: 0, maxLength: 16 }), { minLength: 0, maxLength: 6 }),
        (oldLines, newLines) => {
          // Sanitize any line-breaks out of each "line" so the diff works cleanly.
          const sanitize = (ls: string[]) =>
            ls.map((l) => l.replace(/\r|\n/g, "")).join("\n") + (ls.length > 0 ? "\n" : "");
          const oldC = sanitize(oldLines);
          const newC = sanitize(newLines);
          const patch = emitUnifiedDiff({
            filePath: "x.txt",
            oldContent: oldC,
            newContent: newC,
          });
          const applied = applyPatch(oldC, patch.unifiedDiff);
          return applied === newC;
        },
      ),
      { numRuns: 75 },
    );
  });
});
