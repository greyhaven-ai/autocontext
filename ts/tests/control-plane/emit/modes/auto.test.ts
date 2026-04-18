import { describe, test, expect } from "vitest";
import { resolveAutoMode } from "../../../../src/control-plane/emit/modes/auto.js";

describe("resolveAutoMode", () => {
  test("picks gh when gh is installed and authenticated", () => {
    const result = resolveAutoMode({
      detect: { gh: () => true, git: () => true },
    });
    expect(result.mode).toBe("gh");
    expect(result.reason).toMatch(/gh/i);
  });

  test("falls back to git when gh fails but git + remote are OK", () => {
    const result = resolveAutoMode({
      detect: { gh: () => false, git: () => true },
    });
    expect(result.mode).toBe("git");
    expect(result.reason).toMatch(/git/i);
  });

  test("falls back to patch-only when neither gh nor git is usable", () => {
    const result = resolveAutoMode({
      detect: { gh: () => false, git: () => false },
    });
    expect(result.mode).toBe("patch-only");
    expect(result.reason).toMatch(/patch[- ]?only|neither|fallback/i);
  });

  test("detection cascade is gh → git → patch-only (not the reverse)", () => {
    // gh+git both work → gh wins.
    const r1 = resolveAutoMode({
      detect: { gh: () => true, git: () => true },
    });
    expect(r1.mode).toBe("gh");
    // git works but gh fails → git wins over patch-only.
    const r2 = resolveAutoMode({
      detect: { gh: () => false, git: () => true },
    });
    expect(r2.mode).toBe("git");
  });
});
