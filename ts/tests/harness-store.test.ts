/**
 * Tests for HarnessStore and SkillPackage harness support (MTS-95).
 */
import { describe, it, expect, beforeEach } from "vitest";
import { mkdirSync, mkdtempSync, readFileSync, existsSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { HarnessStore } from "../src/knowledge/harness-store.js";
import { SkillPackage } from "../src/knowledge/skill-package.js";

describe("HarnessStore", () => {
  let knowledgeRoot: string;
  let store: HarnessStore;

  beforeEach(() => {
    knowledgeRoot = mkdtempSync(join(tmpdir(), "mts-harness-test-"));
    store = new HarnessStore(knowledgeRoot, "grid_ctf");
  });

  describe("listHarness", () => {
    it("returns empty for nonexistent dir", () => {
      expect(store.listHarness()).toEqual([]);
    });

    it("lists .py files without extension", () => {
      const dir = join(knowledgeRoot, "grid_ctf", "harness");
      mkdirSync(dir, { recursive: true });
      writeFileSync(join(dir, "validate_move.py"), "def v(): ...");
      writeFileSync(join(dir, "score_action.py"), "def s(): ...");
      expect(store.listHarness()).toEqual(["score_action", "validate_move"]);
    });
  });

  describe("writeVersioned", () => {
    it("creates file and version entry", () => {
      const path = store.writeVersioned("validate_move", "def v(): ...", 1);
      expect(existsSync(path)).toBe(true);
      expect(readFileSync(path, "utf-8")).toBe("def v(): ...");
      const versions = store.getVersions();
      expect(versions.validate_move).toEqual({ version: 1, generation: 1 });
    });

    it("archives previous version on second write", () => {
      store.writeVersioned("validate_move", "v1", 1);
      store.writeVersioned("validate_move", "v2", 2);
      const archiveDir = join(knowledgeRoot, "grid_ctf", "harness", "_archive");
      expect(existsSync(archiveDir)).toBe(true);
      const archives = readdirSync(archiveDir);
      expect(archives.length).toBeGreaterThanOrEqual(1);
    });

    it("increments version number", () => {
      store.writeVersioned("validate_move", "v1", 1);
      store.writeVersioned("validate_move", "v2", 2);
      const versions = store.getVersions();
      expect(versions.validate_move.version).toBe(2);
      expect(versions.validate_move.generation).toBe(2);
    });

    it("tracks multiple harnesses independently", () => {
      store.writeVersioned("validate_move", "m1", 1);
      store.writeVersioned("score_action", "s1", 1);
      const versions = store.getVersions();
      expect(versions.validate_move).toBeDefined();
      expect(versions.score_action).toBeDefined();
    });

    it.each(["", "../escape", "bad/name", "contains space", "123abc"])(
      "rejects invalid harness name %s",
      (name) => {
        expect(() => store.writeVersioned(name, "code", 1)).toThrow("invalid harness name");
      },
    );
  });

  describe("rollback", () => {
    it("returns null when no archive exists", () => {
      store.writeVersioned("validate_move", "v1", 1);
      expect(store.rollback("validate_move")).toBeNull();
    });

    it("restores previous version", () => {
      store.writeVersioned("validate_move", "v1", 1);
      store.writeVersioned("validate_move", "v2", 2);
      const result = store.rollback("validate_move");
      expect(result).toBe("v1");
    });

    it("updates current file on rollback", () => {
      store.writeVersioned("validate_move", "v1", 1);
      store.writeVersioned("validate_move", "v2", 2);
      store.rollback("validate_move");
      expect(store.read("validate_move")).toBe("v1");
    });

    it("returns null for nonexistent harness", () => {
      expect(store.rollback("nonexistent")).toBeNull();
    });

    it("uses numeric archive order for rollback after v10", () => {
      for (let i = 1; i <= 11; i += 1) {
        store.writeVersioned("validate_move", `v${i}`, i);
      }
      const result = store.rollback("validate_move");
      expect(result).toBe("v10");
      expect(store.read("validate_move")).toBe("v10");
    });

    it.each(["", "../escape", "bad/name", "contains space", "123abc"])(
      "rejects invalid rollback name %s",
      (name) => {
        expect(() => store.rollback(name)).toThrow("invalid harness name");
      },
    );
  });

  describe("read", () => {
    it("returns null for nonexistent file", () => {
      expect(store.read("nonexistent")).toBeNull();
    });

    it("returns file contents", () => {
      store.writeVersioned("validate_move", "code here", 1);
      expect(store.read("validate_move")).toBe("code here");
    });

    it.each(["", "../escape", "bad/name", "contains space", "123abc"])(
      "rejects invalid read name %s",
      (name) => {
        expect(() => store.read(name)).toThrow("invalid harness name");
      },
    );
  });
});

describe("SkillPackage harness support", () => {
  it("toDict includes harness field", () => {
    const pkg = new SkillPackage({
      scenarioName: "grid_ctf",
      displayName: "Grid Ctf",
      description: "test",
      playbook: "pb",
      lessons: [],
      bestStrategy: null,
      bestScore: 0,
      bestElo: 1500,
      hints: "",
      harness: { validate_move: "def v(): ..." },
    });
    const d = pkg.toDict();
    expect(d.harness).toEqual({ validate_move: "def v(): ..." });
  });

  it("toDict has empty harness by default", () => {
    const pkg = new SkillPackage({
      scenarioName: "test",
      displayName: "Test",
      description: "desc",
      playbook: "pb",
      lessons: [],
      bestStrategy: null,
      bestScore: 0,
      bestElo: 1500,
      hints: "",
    });
    const d = pkg.toDict();
    expect(d.harness).toEqual({});
  });

  it("toSkillMarkdown includes harness section when present", () => {
    const pkg = new SkillPackage({
      scenarioName: "grid_ctf",
      displayName: "Grid Ctf",
      description: "test",
      playbook: "pb",
      lessons: [],
      bestStrategy: null,
      bestScore: 0,
      bestElo: 1500,
      hints: "",
      harness: { validate_move: "def v(): ..." },
    });
    const md = pkg.toSkillMarkdown();
    expect(md).toContain("## Harness Validators");
    expect(md).toContain("### validate_move");
    expect(md).toContain("def v(): ...");
  });

  it("toSkillMarkdown omits harness section when empty", () => {
    const pkg = new SkillPackage({
      scenarioName: "grid_ctf",
      displayName: "Grid Ctf",
      description: "test",
      playbook: "pb",
      lessons: [],
      bestStrategy: null,
      bestScore: 0,
      bestElo: 1500,
      hints: "",
    });
    const md = pkg.toSkillMarkdown();
    expect(md).not.toContain("## Harness Validators");
  });
});
