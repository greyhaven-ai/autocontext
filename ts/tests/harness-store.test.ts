/**
 * Tests for HarnessStore and SkillPackage harness support (AC-95).
 */
import { describe, it, expect, beforeEach } from "vitest";
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  existsSync,
  readdirSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { HarnessStore } from "../src/knowledge/harness-store.js";
import { SkillPackage } from "../src/knowledge/skill-package.js";

describe("HarnessStore", () => {
  let knowledgeRoot: string;
  let store: HarnessStore;

  beforeEach(() => {
    knowledgeRoot = mkdtempSync(join(tmpdir(), "autocontext-harness-test-"));
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

    it("allows the full harness capacity plus bounded structural entries", () => {
      const dir = join(knowledgeRoot, "grid_ctf", "harness");
      mkdirSync(join(dir, "_archive"), { recursive: true });
      writeFileSync(join(dir, "harness_version.json"), "{}", "utf-8");
      for (let index = 0; index < 2_048; index += 1) {
        writeFileSync(join(dir, `h${index}.py`), "pass\n", "utf-8");
      }

      expect(store.listHarness()).toHaveLength(2_048);
    }, 15_000);

    it("rejects more than the physical harness-file capacity", () => {
      const dir = join(knowledgeRoot, "grid_ctf", "harness");
      mkdirSync(dir, { recursive: true });
      for (let index = 0; index < 2_049; index += 1) {
        writeFileSync(join(dir, `h${index}.py`), "pass\n", "utf-8");
      }

      expect(() => store.listHarness()).toThrow("exceeds 2048 harness file limit");
      expect(() => store.writeVersioned("new_harness", "safe", 1))
        .toThrow("reached 2048 harness file limit");
      expect(readdirSync(dir)).toHaveLength(2_049);
    }, 15_000);
  });

  describe("writeVersioned", () => {
    it("creates file and version entry", () => {
      const path = store.writeVersioned("validate_move", "def v(): ...", 1);
      expect(existsSync(path)).toBe(true);
      expect(readFileSync(path, "utf-8")).toBe("def v(): ...");
      const versions = store.getVersions();
      expect(Object.getPrototypeOf(versions)).toBeNull();
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

    it("returns empty version metadata for malformed harness_version.json", () => {
      const dir = join(knowledgeRoot, "grid_ctf", "harness");
      mkdirSync(dir, { recursive: true });
      writeFileSync(join(dir, "harness_version.json"), JSON.stringify(["bad"]), "utf-8");

      expect(store.getVersions()).toEqual({});
    });

    it("fails closed without replacing valid over-limit version metadata", () => {
      const dir = join(knowledgeRoot, "grid_ctf", "harness");
      mkdirSync(dir, { recursive: true });
      const versions = Object.fromEntries(Array.from(
        { length: 2_049 },
        (_, index) => [`h${index}`, { version: 1, generation: index }],
      ));
      const metadataPath = join(dir, "harness_version.json");
      const originalMetadata = JSON.stringify(versions);
      writeFileSync(metadataPath, originalMetadata, "utf-8");

      expect(() => store.getVersions()).toThrow("exceeds 2048 entry limit");
      expect(() => store.writeVersioned("new_harness", "safe", 1))
        .toThrow("exceeds 2048 entry limit");
      expect(readFileSync(metadataPath, "utf-8")).toBe(originalMetadata);
      expect(existsSync(join(dir, "new_harness.py"))).toBe(false);
    });

    it("rejects a new harness before crossing the version-entry limit", () => {
      const dir = join(knowledgeRoot, "grid_ctf", "harness");
      mkdirSync(dir, { recursive: true });
      const versions = Object.fromEntries(Array.from(
        { length: 2_048 },
        (_, index) => [`h${index}`, { version: 1, generation: index }],
      ));
      const metadataPath = join(dir, "harness_version.json");
      const originalMetadata = JSON.stringify(versions);
      writeFileSync(metadataPath, originalMetadata, "utf-8");

      expect(() => store.writeVersioned("new_harness", "safe", 1))
        .toThrow("reached 2048 entry limit");
      expect(readFileSync(metadataPath, "utf-8")).toBe(originalMetadata);
      expect(existsSync(join(dir, "new_harness.py"))).toBe(false);
    });

    it("does not let inherited object names bypass the final entry limit", () => {
      const dir = join(knowledgeRoot, "grid_ctf", "harness");
      mkdirSync(dir, { recursive: true });
      const versions = Object.fromEntries(Array.from(
        { length: 2_048 },
        (_, index) => [`h${index}`, { version: 1, generation: index }],
      ));
      const metadataPath = join(dir, "harness_version.json");
      const originalMetadata = JSON.stringify(versions);
      writeFileSync(metadataPath, originalMetadata, "utf-8");

      expect(() => store.writeVersioned("toString", "safe", 1))
        .toThrow("reached 2048 entry limit");
      expect(readFileSync(metadataPath, "utf-8")).toBe(originalMetadata);
      expect(existsSync(join(dir, "toString.py"))).toBe(false);
    });

    it("rejects a new harness before structural entries cross the scan limit", () => {
      const dir = join(knowledgeRoot, "grid_ctf", "harness");
      mkdirSync(join(dir, "_archive"), { recursive: true });
      const versions = Object.fromEntries(Array.from(
        { length: 2_047 },
        (_, index) => [`h${index}`, { version: 1, generation: index }],
      ));
      const metadataPath = join(dir, "harness_version.json");
      const originalMetadata = JSON.stringify(versions);
      writeFileSync(metadataPath, originalMetadata, "utf-8");
      for (let index = 0; index < 2_047; index += 1) {
        writeFileSync(join(dir, `h${index}.py`), "pass\n", "utf-8");
      }
      writeFileSync(join(dir, "untracked.txt"), "junk", "utf-8");

      expect(() => store.writeVersioned("new_harness", "safe", 1))
        .toThrow("harness directory reached 2050 entry limit");
      expect(readFileSync(metadataPath, "utf-8")).toBe(originalMetadata);
      expect(existsSync(join(dir, "new_harness.py"))).toBe(false);
    }, 15_000);

    it("reserves space for missing metadata before adding a new source", () => {
      const dir = join(knowledgeRoot, "grid_ctf", "harness");
      mkdirSync(dir, { recursive: true });
      for (let index = 0; index < 2_048; index += 1) {
        writeFileSync(join(dir, `junk${index}.txt`), "junk", "utf-8");
      }
      writeFileSync(join(dir, "existing.py"), "pass\n", "utf-8");

      expect(() => store.writeVersioned("new_harness", "safe", 1))
        .toThrow("reached 2050 entry limit");
      expect(existsSync(join(dir, "new_harness.py"))).toBe(false);
      expect(existsSync(join(dir, "harness_version.json"))).toBe(false);
      expect(readdirSync(dir)).toHaveLength(2_049);
    }, 15_000);

    it("reserves space for the first archive directory on replacement", () => {
      const dir = join(knowledgeRoot, "grid_ctf", "harness");
      mkdirSync(dir, { recursive: true });
      writeFileSync(join(dir, "existing.py"), "old\n", "utf-8");
      writeFileSync(
        join(dir, "harness_version.json"),
        JSON.stringify({ existing: { version: 1, generation: 0 } }),
        "utf-8",
      );
      for (let index = 0; index < 2_048; index += 1) {
        writeFileSync(join(dir, `junk${index}.txt`), "junk", "utf-8");
      }

      expect(() => store.writeVersioned("existing", "new", 1))
        .toThrow("reached 2050 entry limit");
      expect(readFileSync(join(dir, "existing.py"), "utf-8")).toBe("old\n");
      expect(existsSync(join(dir, "_archive"))).toBe(false);
      expect(readdirSync(dir)).toHaveLength(2_050);
    }, 15_000);

    it("rejects updates when the archive has reached its scan limit", () => {
      store.writeVersioned("existing", "old", 0);
      const dir = join(knowledgeRoot, "grid_ctf", "harness");
      const archiveDir = join(dir, "_archive");
      mkdirSync(archiveDir);
      for (let index = 0; index < 10_000; index += 1) {
        writeFileSync(join(archiveDir, `junk${index}.txt`), "junk", "utf-8");
      }
      const metadataPath = join(dir, "harness_version.json");
      const originalMetadata = readFileSync(metadataPath, "utf-8");

      expect(() => store.writeVersioned("existing", "new", 1))
        .toThrow("archive reached 10000 entry limit");
      expect(readFileSync(join(dir, "existing.py"), "utf-8")).toBe("old");
      expect(readFileSync(metadataPath, "utf-8")).toBe(originalMetadata);
      expect(readdirSync(archiveDir)).toHaveLength(10_000);
    }, 30_000);

    it.each(["", "../escape", "bad/name", "contains space", "123abc", "__proto__", "constructor"])(
      "rejects invalid harness name %s",
      (name) => {
        expect(() => store.writeVersioned(name, "code", 1)).toThrow("invalid harness name");
      },
    );

    it("rejects a symbolic-link knowledge root", () => {
      const container = mkdtempSync(join(tmpdir(), "autocontext-harness-root-link-"));
      const outside = join(container, "outside");
      mkdirSync(outside);
      const linkedRoot = join(container, "knowledge");
      symlinkSync(outside, linkedRoot, "dir");

      expect(() => new HarnessStore(linkedRoot, "grid_ctf")
        .writeVersioned("validate_move", "safe", 1)).toThrow("symbolic-link");
      expect(existsSync(join(outside, "grid_ctf"))).toBe(false);
    });

    it("rejects symbolic-link scenario and harness directory components", () => {
      const outside = mkdtempSync(join(tmpdir(), "autocontext-harness-outside-"));
      symlinkSync(outside, join(knowledgeRoot, "grid_ctf"), "dir");
      expect(() => store.writeVersioned("validate_move", "safe", 1)).toThrow("symbolic-link");
      expect(readdirSync(outside)).toEqual([]);

      const secondRoot = mkdtempSync(join(tmpdir(), "autocontext-harness-root-"));
      const scenario = join(secondRoot, "grid_ctf");
      mkdirSync(scenario);
      symlinkSync(outside, join(scenario, "harness"), "dir");
      expect(() => new HarnessStore(secondRoot, "grid_ctf")
        .writeVersioned("validate_move", "safe", 1)).toThrow("symbolic-link");
      expect(readdirSync(outside)).toEqual([]);
    });

    it("rejects symbolic-link archive directories without changing the current source", () => {
      store.writeVersioned("validate_move", "v1", 1);
      const outside = mkdtempSync(join(tmpdir(), "autocontext-harness-archive-outside-"));
      const harnessDir = join(knowledgeRoot, "grid_ctf", "harness");
      symlinkSync(outside, join(harnessDir, "_archive"), "dir");

      expect(() => store.writeVersioned("validate_move", "v2", 2)).toThrow("symbolic-link");
      expect(store.read("validate_move")).toBe("v1");
      expect(readdirSync(outside)).toEqual([]);
    });

    it("rejects symbolic-link harness and archive file targets", () => {
      const harnessDir = join(knowledgeRoot, "grid_ctf", "harness");
      mkdirSync(harnessDir, { recursive: true });
      const outsideFile = join(knowledgeRoot, "outside.py");
      writeFileSync(outsideFile, "sentinel", "utf-8");
      symlinkSync(outsideFile, join(harnessDir, "validate_move.py"));

      expect(() => store.writeVersioned("validate_move", "replacement", 1)).toThrow("symbolic-link");
      expect(readFileSync(outsideFile, "utf-8")).toBe("sentinel");

      const secondRoot = mkdtempSync(join(tmpdir(), "autocontext-harness-archive-file-"));
      const secondStore = new HarnessStore(secondRoot, "grid_ctf");
      secondStore.writeVersioned("validate_move", "v1", 1);
      const archiveDir = join(secondRoot, "grid_ctf", "harness", "_archive");
      mkdirSync(archiveDir);
      symlinkSync(outsideFile, join(archiveDir, "v1_validate_move.py"));
      expect(() => secondStore.writeVersioned("validate_move", "v2", 2)).toThrow("symbolic-link");
      expect(secondStore.read("validate_move")).toBe("v1");
      expect(readFileSync(outsideFile, "utf-8")).toBe("sentinel");
    });

    it("rejects source larger than the bounded harness limit", () => {
      expect(() => store.writeVersioned("validate_move", "x".repeat(1024 * 1024 + 1), 1))
        .toThrow("source exceeds");
      expect(store.read("validate_move")).toBeNull();
    });
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
