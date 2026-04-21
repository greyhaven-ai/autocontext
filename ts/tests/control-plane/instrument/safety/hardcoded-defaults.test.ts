/**
 * A2-I Layer 3 — hardcoded-defaults pattern list (spec §5.1 step 1).
 *
 * The canonical location of the non-configurable skip-pattern list is
 * `safety/hardcoded-defaults.ts`. The scanner's walker imports from here;
 * this layer test verifies the pattern list's completeness + match behavior.
 */
import { describe, test, expect } from "vitest";
import ignore from "ignore";
import { HARDCODED_DEFAULT_PATTERNS } from "../../../../src/control-plane/instrument/safety/hardcoded-defaults.js";

describe("HARDCODED_DEFAULT_PATTERNS — spec §5.1 completeness", () => {
  test("exports the full set of non-negotiable skip patterns", () => {
    // Every documented-in-spec pattern family must be present. We test by
    // matching behavior (gitignore semantics), not by string identity — the
    // pattern LIST may include a couple of gitignore-dialect variants per
    // family (e.g., `.env` + `.env*`, `node_modules/` + `node_modules/**`).
    const i = ignore().add([...HARDCODED_DEFAULT_PATTERNS]);
    expect(i.ignores(".env")).toBe(true);
    expect(i.ignores(".env.local")).toBe(true);
    expect(i.ignores(".env.production")).toBe(true);
    expect(i.ignores(".venv/lib/python3.12/site.py")).toBe(true);
    expect(i.ignores("node_modules/foo/index.js")).toBe(true);
    expect(i.ignores(".git/HEAD")).toBe(true);
    expect(i.ignores(".autocontext/runs/x.json")).toBe(true);
    expect(i.ignores("id.pem")).toBe(true);
    expect(i.ignores("server.key")).toBe(true);
    expect(i.ignores("api.secret")).toBe(true);
    expect(i.ignores("cert.p12")).toBe(true);
    expect(i.ignores("root.crt")).toBe(true);
    expect(i.ignores("client.cer")).toBe(true);
  });

  test("does NOT match legitimate source files with similar names", () => {
    const i = ignore().add([...HARDCODED_DEFAULT_PATTERNS]);
    // Names that LOOK like they might collide with the patterns but don't.
    expect(i.ignores("envelope.py")).toBe(false);
    expect(i.ignores("empirical.ts")).toBe(false);
    expect(i.ignores("src/envmanager.ts")).toBe(false);
    expect(i.ignores("nodemodules.md")).toBe(false);
    expect(i.ignores("src/keymap.py")).toBe(false);
    expect(i.ignores("src/my.keyword.ts")).toBe(false);
    expect(i.ignores("git-helpers.ts")).toBe(false);
  });

  test("is a frozen readonly tuple (no mutation)", () => {
    expect(Object.isFrozen(HARDCODED_DEFAULT_PATTERNS)).toBe(true);
  });

  test("contains at least one entry per documented pattern family", () => {
    // Sanity: pattern strings include the substrings the spec names. This is
    // a syntactic companion to the behavior checks above.
    const joined = HARDCODED_DEFAULT_PATTERNS.join("|");
    expect(joined).toMatch(/\.env/);
    expect(joined).toMatch(/\.venv/);
    expect(joined).toMatch(/node_modules/);
    expect(joined).toMatch(/\.git/);
    expect(joined).toMatch(/\.autocontext/);
    expect(joined).toMatch(/\.pem/);
    expect(joined).toMatch(/\.key/);
    expect(joined).toMatch(/\.secret/);
    expect(joined).toMatch(/\.p12/);
    expect(joined).toMatch(/\.crt/);
    expect(joined).toMatch(/\.cer/);
  });
});
