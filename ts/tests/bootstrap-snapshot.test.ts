/**
 * AC-503: Environment snapshot bootstrapping tests (TypeScript).
 */

import { describe, it, expect } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  collectSnapshot,
  collectCore,
  collectRuntimes,
  collectPackages,
  collectFilesystem,
  collectGit,
  collectSystem,
} from "../src/bootstrap/collector.js";
import {
  redactSnapshot,
  DEFAULT_REDACTION,
} from "../src/bootstrap/redactor.js";
import {
  renderPromptSection,
  renderFullJson,
} from "../src/bootstrap/renderer.js";
import type {
  EnvironmentSnapshot,
  PackageInfo,
} from "../src/bootstrap/snapshot.js";

function makeSnapshot(
  overrides: Partial<EnvironmentSnapshot> = {},
): EnvironmentSnapshot {
  return {
    workingDirectory: "/home/user/project",
    osName: "linux",
    osVersion: "6.1.0",
    shell: "/bin/zsh",
    hostname: "dev-machine",
    username: "testuser",
    pythonVersion: "3.13.1",
    availableRuntimes: { node: "v20.1.0" },
    installedPackages: [{ name: "autoctx", version: "0.3.5" }],
    lockfilesFound: ["bun.lock"],
    notableFiles: ["package.json", "README.md", "src/"],
    directoryCount: 5,
    fileCount: 12,
    gitBranch: "main",
    gitCommit: "abc1234",
    gitDirty: false,
    gitWorktree: false,
    memoryTotalMb: 32768,
    memoryAvailableMb: 16384,
    diskFreeGb: 142.3,
    cpuCount: 16,
    collectedAt: "2026-04-06T00:00:00Z",
    collectorVersion: "1.0.0",
    redactedFields: [],
    ...overrides,
  };
}

describe("Collector", () => {
  it("collectSnapshot returns all required fields", () => {
    const snap = collectSnapshot();
    expect(snap.workingDirectory).toBeTruthy();
    expect(snap.osName).toBeTruthy();
    expect(snap.cpuCount).toBeGreaterThan(0);
    expect(snap.collectedAt).toBeTruthy();
  });

  it("collectCore includes working directory", () => {
    const core = collectCore();
    expect(core.workingDirectory).toBeTruthy();
  });

  it("collectCore includes os info", () => {
    const core = collectCore();
    expect(core.osName).toBeTruthy();
    expect(core.osVersion).toBeTruthy();
  });

  it("collectRuntimes finds node", () => {
    const rt = collectRuntimes();
    expect(rt.availableRuntimes).toHaveProperty("node");
  });

  it("collectPackages returns array", () => {
    const pkg = collectPackages();
    expect(Array.isArray(pkg.installedPackages)).toBe(true);
  });

  it("collectFilesystem caps at 50 files", () => {
    const tmp = mkdtempSync(join(tmpdir(), "ac503-fs-"));
    try {
      for (let i = 0; i < 60; i++)
        writeFileSync(join(tmp, `file_${i}.txt`), "x");
      const fs = collectFilesystem(tmp);
      expect(fs.notableFiles.length).toBeLessThanOrEqual(50);
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });

  it("collectGit returns branch in repo", () => {
    const git = collectGit();
    expect(git.gitBranch).toBeTruthy();
  });

  it("collectSystem returns positive values", () => {
    const sys = collectSystem();
    expect(sys.memoryTotalMb).toBeGreaterThan(0);
    expect(sys.cpuCount).toBeGreaterThan(0);
  });

  it("collector never throws", () => {
    expect(() => collectSnapshot()).not.toThrow();
  });
});

describe("Redactor", () => {
  it("redacts hostname when configured", () => {
    const snap = makeSnapshot({ hostname: "secret-host" });
    const result = redactSnapshot(snap, {
      redactHostname: true,
      redactUsername: false,
      redactPaths: false,
    });
    expect(result.hostname).toBe("[REDACTED]");
  });

  it("redacts username when configured", () => {
    const snap = makeSnapshot({ username: "secretuser" });
    const result = redactSnapshot(snap, {
      redactHostname: false,
      redactUsername: true,
      redactPaths: false,
    });
    expect(result.username).toBe("[REDACTED]");
  });

  it("strips absolute paths to relative", () => {
    const snap = makeSnapshot({ workingDirectory: "/home/user/project" });
    const result = redactSnapshot(snap, {
      redactHostname: false,
      redactUsername: false,
      redactPaths: true,
    });
    expect(result.workingDirectory).toBe(".");
  });

  it("redacts absolute shell paths when path redaction is enabled", () => {
    const snap = makeSnapshot({ shell: "/bin/zsh" });
    const result = redactSnapshot(snap, {
      redactHostname: false,
      redactUsername: false,
      redactPaths: true,
    });
    expect(result.shell).toBe("zsh");
    expect(result.redactedFields).toContain("shell");
  });

  it("records redacted field names", () => {
    const snap = makeSnapshot();
    const result = redactSnapshot(snap, DEFAULT_REDACTION);
    expect(result.redactedFields).toContain("hostname");
    expect(result.redactedFields).toContain("username");
  });

  it("preserves all fields when redaction disabled", () => {
    const snap = makeSnapshot({ hostname: "myhost", username: "myuser" });
    const result = redactSnapshot(snap, {
      redactHostname: false,
      redactUsername: false,
      redactPaths: false,
    });
    expect(result.hostname).toBe("myhost");
    expect(result.username).toBe("myuser");
    expect(result.redactedFields).toEqual([]);
  });
});

describe("Renderer", () => {
  it("prompt section is compact", () => {
    const snap = makeSnapshot();
    const output = renderPromptSection(snap);
    expect(output.length).toBeLessThanOrEqual(600);
  });

  it("prompt section includes python version", () => {
    const snap = makeSnapshot({ pythonVersion: "3.13.1" });
    expect(renderPromptSection(snap)).toContain("3.13.1");
  });

  it("prompt section includes git info", () => {
    const snap = makeSnapshot({ gitBranch: "main", gitCommit: "abc1234" });
    const output = renderPromptSection(snap);
    expect(output).toContain("main");
    expect(output).toContain("abc1234");
  });

  it("prompt section handles null git", () => {
    const snap = makeSnapshot({ gitBranch: null, gitCommit: null });
    const output = renderPromptSection(snap);
    expect(output).not.toContain("Git:");
  });

  it("full JSON is valid JSON", () => {
    const snap = makeSnapshot();
    const output = renderFullJson(snap);
    expect(() => JSON.parse(output)).not.toThrow();
  });

  it("full JSON roundtrips", () => {
    const snap = makeSnapshot();
    const output = renderFullJson(snap);
    const parsed = JSON.parse(output) as EnvironmentSnapshot;
    expect(parsed.pythonVersion).toBe(snap.pythonVersion);
    expect(parsed.osName).toBe(snap.osName);
  });
});
