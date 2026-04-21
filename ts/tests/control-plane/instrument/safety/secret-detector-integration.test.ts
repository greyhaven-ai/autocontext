/**
 * A2-I Layer 3 integration — SourceFile load path wires secret-detector.
 *
 * Layer 2 stubbed `hasSecretLiteral: false`. Layer 3's detector fills the flag
 * at SourceFile construction time. This test asserts the wiring:
 *
 *   - file with an embedded fake AWS key → hasSecretLiteral: true + secretMatches populated
 *   - file without any pattern match → hasSecretLiteral: false + secretMatches empty
 */
import { describe, test, expect } from "vitest";
import { fromBytes } from "../../../../src/control-plane/instrument/scanner/source-file.js";

describe("SourceFile — secret-detector wiring", () => {
  test("file with fake AWS key has hasSecretLiteral=true and non-empty secretMatches", () => {
    const bytes = Buffer.from(
      [
        "import os",
        `AWS = "AKIAIOSFODNN7EXAMPLE"`,
        "print(AWS)",
      ].join("\n"),
      "utf-8",
    );
    const sf = fromBytes({ path: "x.py", language: "python", bytes });
    expect(sf.hasSecretLiteral).toBe(true);
    expect(sf.secretMatches.length).toBeGreaterThan(0);
    expect(sf.secretMatches[0]!.pattern).toBe("aws-access-key");
  });

  test("benign file has hasSecretLiteral=false and empty secretMatches", () => {
    const bytes = Buffer.from(
      ["import os", "print('hello')"].join("\n"),
      "utf-8",
    );
    const sf = fromBytes({ path: "ok.py", language: "python", bytes });
    expect(sf.hasSecretLiteral).toBe(false);
    expect(sf.secretMatches).toEqual([]);
  });
});
