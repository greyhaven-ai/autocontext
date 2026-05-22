import { describe, expect, test } from "vitest";
import {
  probeArtifactContract,
  probeCleanupContract,
  probeDirectoryContract,
  probeDistributedContract,
  probeServiceContract,
  probeTerminalContract,
} from "../../../src/control-plane/contract-probes/index.js";

describe("probeDirectoryContract", () => {
  test("reports unexpected and missing verifier-facing files", () => {
    const result = probeDirectoryContract({
      presentFiles: ["solution.txt", "main", "trace.log"],
      requiredFiles: ["solution.txt", "manifest.json"],
      allowedFiles: ["solution.txt", "manifest.json"],
      ignoredPatterns: [/^trace\./],
    });

    expect(result.passed).toBe(false);
    expect(result.failures).toEqual([
      {
        kind: "unexpected-file",
        path: "main",
        message: "unexpected file main",
      },
      {
        kind: "missing-file",
        path: "manifest.json",
        message: "required file manifest.json is missing",
      },
    ]);
  });
});

describe("probeTerminalContract", () => {
  test("passes when exit code matches and all required patterns match", () => {
    const result = probeTerminalContract({
      exitCode: 0,
      stdout: "All checks passed.\n",
      stderr: "",
      expectedExitCode: 0,
      requiredStdoutPatterns: [/checks passed/],
    });
    expect(result.passed).toBe(true);
    expect(result.failures).toEqual([]);
  });

  test("flags wrong exit code", () => {
    const result = probeTerminalContract({
      exitCode: 1,
      stdout: "",
      stderr: "error",
      expectedExitCode: 0,
    });
    expect(result.passed).toBe(false);
    expect(result.failures[0]).toMatchObject({ kind: "unexpected-exit-code" });
  });

  test("flags a missing required stdout pattern", () => {
    const result = probeTerminalContract({
      exitCode: 0,
      stdout: "Done.\n",
      stderr: "",
      requiredStdoutPatterns: [/All checks passed/],
    });
    expect(result.passed).toBe(false);
    expect(result.failures[0]).toMatchObject({ kind: "missing-stdout-pattern" });
  });

  test("flags a forbidden stderr pattern", () => {
    const result = probeTerminalContract({
      exitCode: 0,
      stdout: "ok",
      stderr: "DeprecationWarning: legacy API",
      forbiddenStderrPatterns: [/DeprecationWarning/],
    });
    expect(result.passed).toBe(false);
    expect(result.failures[0]).toMatchObject({ kind: "forbidden-stderr-pattern" });
  });

  test("defaults expected exit code to 0", () => {
    const result = probeTerminalContract({
      exitCode: 2,
      stdout: "",
      stderr: "",
    });
    expect(result.passed).toBe(false);
    expect(result.failures[0]).toMatchObject({ kind: "unexpected-exit-code" });
  });
});

describe("probeServiceContract", () => {
  test("passes when required endpoints are all listening", () => {
    const result = probeServiceContract({
      observed: [
        { host: "127.0.0.1", port: 8080, protocol: "tcp" },
        { host: "127.0.0.1", port: 9090, protocol: "tcp" },
      ],
      required: [{ host: "127.0.0.1", port: 8080, protocol: "tcp" }],
    });
    expect(result.passed).toBe(true);
  });

  test("flags a missing required endpoint", () => {
    const result = probeServiceContract({
      observed: [{ host: "127.0.0.1", port: 8080, protocol: "tcp" }],
      required: [{ host: "127.0.0.1", port: 9090, protocol: "tcp" }],
    });
    expect(result.passed).toBe(false);
    expect(result.failures[0]).toMatchObject({ kind: "missing-endpoint" });
  });

  test("flags an extra endpoint when an allowed list is given", () => {
    const result = probeServiceContract({
      observed: [
        { host: "127.0.0.1", port: 8080, protocol: "tcp" },
        { host: "127.0.0.1", port: 6379, protocol: "tcp" },
      ],
      required: [{ host: "127.0.0.1", port: 8080, protocol: "tcp" }],
      allowed: [{ host: "127.0.0.1", port: 8080, protocol: "tcp" }],
    });
    expect(result.passed).toBe(false);
    expect(result.failures[0]).toMatchObject({ kind: "unexpected-endpoint" });
  });

  test("distinguishes host binding (127.0.0.1 vs 0.0.0.0)", () => {
    // Binding on 0.0.0.0 when 127.0.0.1 was required is a wrong-interface failure,
    // not a missing-endpoint failure -- verifiers that check loopback-only will
    // fail differently from those that check exposure.
    const result = probeServiceContract({
      observed: [{ host: "0.0.0.0", port: 8080, protocol: "tcp" }],
      required: [{ host: "127.0.0.1", port: 8080, protocol: "tcp" }],
    });
    expect(result.passed).toBe(false);
    expect(result.failures[0]).toMatchObject({ kind: "wrong-interface" });
  });

  test("defaults protocol to tcp when not specified", () => {
    const result = probeServiceContract({
      observed: [{ host: "127.0.0.1", port: 8080 }],
      required: [{ host: "127.0.0.1", port: 8080, protocol: "tcp" }],
    });
    expect(result.passed).toBe(true);
  });
});

describe("probeArtifactContract", () => {
  test("passes a UTF-8 LF file with all required substrings", () => {
    const result = probeArtifactContract({
      path: "config.txt",
      content: "key=value\nlog_format detailed\n",
      expectedLineEnding: "lf",
      requiredSubstrings: ["log_format detailed"],
    });
    expect(result.passed).toBe(true);
  });

  test("flags missing required substring", () => {
    const result = probeArtifactContract({
      path: "config.txt",
      content: "key=value\n",
      requiredSubstrings: ["log_format detailed"],
    });
    expect(result.passed).toBe(false);
    expect(result.failures[0]).toMatchObject({ kind: "missing-substring" });
  });

  test("flags forbidden substring (e.g., placeholder left behind)", () => {
    const result = probeArtifactContract({
      path: "manifest.json",
      content: '{"name": "TODO_FILL_IN"}',
      forbiddenSubstrings: ["TODO_FILL_IN"],
    });
    expect(result.passed).toBe(false);
    expect(result.failures[0]).toMatchObject({ kind: "forbidden-substring" });
  });

  test("flags a CRLF line ending when LF is required", () => {
    const result = probeArtifactContract({
      path: "config.txt",
      content: "key=value\r\nlog_format detailed\r\n",
      expectedLineEnding: "lf",
    });
    expect(result.passed).toBe(false);
    expect(result.failures[0]).toMatchObject({ kind: "wrong-line-ending" });
  });

  test("flags missing JSON field via dot-path", () => {
    const result = probeArtifactContract({
      path: "manifest.json",
      content: JSON.stringify({ name: "x", version: "1.0" }),
      requiredJsonFields: ["name", "license"],
    });
    expect(result.passed).toBe(false);
    expect(result.failures[0]).toMatchObject({ kind: "missing-json-field", path: "license" });
  });

  test("supports nested JSON field dot-paths", () => {
    const result = probeArtifactContract({
      path: "manifest.json",
      content: JSON.stringify({ pkg: { name: "x" } }),
      requiredJsonFields: ["pkg.name", "pkg.version"],
    });
    expect(result.passed).toBe(false);
    expect(result.failures).toHaveLength(1);
    expect(result.failures[0]).toMatchObject({ kind: "missing-json-field", path: "pkg.version" });
  });

  test("flags invalid JSON when fields are required", () => {
    const result = probeArtifactContract({
      path: "manifest.json",
      content: "not json at all",
      requiredJsonFields: ["name"],
    });
    expect(result.passed).toBe(false);
    expect(result.failures[0]).toMatchObject({ kind: "invalid-json" });
  });
});

describe("probeCleanupContract", () => {
  test("passes when the directory listing has no leftover artifacts", () => {
    const result = probeCleanupContract({
      entries: [{ path: "solution.txt" }, { path: "manifest.json" }, { path: "src/main.py" }],
    });
    expect(result.passed).toBe(true);
    expect(result.failures).toEqual([]);
  });

  test("flags broken symlinks even when symlinks are allowed", () => {
    const result = probeCleanupContract({
      entries: [
        { path: "solution.txt" },
        { path: "broken-link", isSymlink: true, symlinkBroken: true },
      ],
    });
    expect(result.passed).toBe(false);
    expect(result.failures).toEqual([
      {
        kind: "broken-symlink",
        path: "broken-link",
        message: "broken-link is a broken symlink (target missing)",
      },
    ]);
  });

  test("flags every symlink when forbidSymlinks is set", () => {
    const result = probeCleanupContract({
      entries: [
        { path: "solution.txt" },
        { path: "alias", isSymlink: true, symlinkTarget: "solution.txt" },
      ],
      forbidSymlinks: true,
    });
    expect(result.passed).toBe(false);
    expect(result.failures).toEqual([
      {
        kind: "stray-symlink",
        path: "alias",
        message: "alias is a symlink (target solution.txt); symlinks are forbidden by contract",
      },
    ]);
  });

  test("flags symlinks whose target is outside the allowlist", () => {
    const result = probeCleanupContract({
      entries: [{ path: "alias", isSymlink: true, symlinkTarget: "/etc/passwd" }],
      allowedSymlinkTargets: ["solution.txt"],
    });
    expect(result.passed).toBe(false);
    expect(result.failures).toEqual([
      {
        kind: "stray-symlink",
        path: "alias",
        message: "alias is a symlink to /etc/passwd; target is not in the allowlist",
      },
    ]);
  });

  test("permits symlinks whose target is on the allowlist", () => {
    const result = probeCleanupContract({
      entries: [
        { path: "alias", isSymlink: true, symlinkTarget: "solution.txt" },
        { path: "solution.txt" },
      ],
      allowedSymlinkTargets: ["solution.txt"],
    });
    expect(result.passed).toBe(true);
  });

  test("flags default sidecar leftovers (.swp, ~, .DS_Store)", () => {
    const result = probeCleanupContract({
      entries: [
        { path: "solution.txt" },
        { path: ".solution.txt.swp" },
        { path: "notes~" },
        { path: ".DS_Store" },
      ],
    });
    expect(result.passed).toBe(false);
    expect(result.failures.map((f) => f.kind).sort()).toEqual([
      "stray-sidecar",
      "stray-sidecar",
      "stray-sidecar",
    ]);
  });

  test("flags default backup leftovers (.bak, .orig)", () => {
    const result = probeCleanupContract({
      entries: [
        { path: "solution.txt" },
        { path: "solution.txt.bak" },
        { path: "manifest.json.orig" },
      ],
    });
    expect(result.passed).toBe(false);
    expect(result.failures.map((f) => f.kind).sort()).toEqual(["stray-backup", "stray-backup"]);
  });

  test("flags lockfiles unconditionally when no age threshold is set", () => {
    const result = probeCleanupContract({
      entries: [{ path: "solution.txt" }, { path: ".lock" }, { path: "build.pid" }],
    });
    expect(result.passed).toBe(false);
    expect(result.failures.map((f) => f.kind).sort()).toEqual(["stale-lockfile", "stale-lockfile"]);
  });

  test("flags lockfiles only when older than maxLockfileAgeMs", () => {
    const now = new Date("2026-05-21T12:00:00Z");
    const fresh = new Date("2026-05-21T11:59:00Z"); // 60s ago
    const stale = new Date("2026-05-21T10:00:00Z"); // 2h ago
    const result = probeCleanupContract({
      entries: [
        { path: "solution.txt" },
        { path: "fresh.lock", mtime: fresh },
        { path: "stale.lock", mtime: stale },
      ],
      now,
      maxLockfileAgeMs: 5 * 60 * 1000, // 5 minutes
    });
    expect(result.passed).toBe(false);
    expect(result.failures).toHaveLength(1);
    expect(result.failures[0]).toMatchObject({
      kind: "stale-lockfile",
      path: "stale.lock",
    });
  });

  test("respects ignoredPatterns the same way as probeDirectoryContract", () => {
    const result = probeCleanupContract({
      entries: [{ path: "solution.txt" }, { path: "trace.swp" }],
      // /^trace\./ mirrors the AC-728 slice 1 directory-probe convention.
      ignoredPatterns: [/^trace\./],
    });
    expect(result.passed).toBe(true);
  });

  test("accepts caller-supplied sidecar and backup pattern overrides", () => {
    const result = probeCleanupContract({
      entries: [{ path: "solution.txt" }, { path: "solution.txt.foo" }],
      backupPatterns: [/\.foo$/],
      sidecarPatterns: [],
    });
    expect(result.passed).toBe(false);
    expect(result.failures[0]).toMatchObject({
      kind: "stray-backup",
      path: "solution.txt.foo",
    });
  });
});

describe("probeDistributedContract", () => {
  // A clean 4-rank parity report: every rank reported the same
  // final-loss hash and the same step count.
  const FOUR_RANK_CLEAN = [
    { rank: 0, steps: 100, observations: { final_loss: "abc", grad_norm: "xyz" } },
    { rank: 1, steps: 100, observations: { final_loss: "abc", grad_norm: "xyz" } },
    { rank: 2, steps: 100, observations: { final_loss: "abc", grad_norm: "xyz" } },
    { rank: 3, steps: 100, observations: { final_loss: "abc", grad_norm: "xyz" } },
  ] as const;

  test("passes when world size, ranks, steps, and observations all agree", () => {
    const result = probeDistributedContract({
      worldSize: 4,
      ranks: FOUR_RANK_CLEAN,
      expectedWorldSize: 4,
      expectedSteps: 100,
      mustMatchAcrossRanks: ["final_loss", "grad_norm"],
    });
    expect(result.passed).toBe(true);
    expect(result.failures).toEqual([]);
  });

  test("flags wrong-world-size when observed world disagrees with expected", () => {
    const result = probeDistributedContract({
      worldSize: 2,
      ranks: [
        { rank: 0, steps: 100, observations: {} },
        { rank: 1, steps: 100, observations: {} },
      ],
      expectedWorldSize: 4,
    });
    expect(result.passed).toBe(false);
    expect(result.failures).toContainEqual({
      kind: "wrong-world-size",
      message: "observed world size 2 does not match expected 4",
    });
  });

  test("flags missing-rank when not every rank in [0, worldSize) reported", () => {
    const result = probeDistributedContract({
      worldSize: 4,
      ranks: [
        { rank: 0, steps: 100, observations: {} },
        { rank: 1, steps: 100, observations: {} },
        // rank 2 missing
        { rank: 3, steps: 100, observations: {} },
      ],
    });
    expect(result.passed).toBe(false);
    expect(result.failures).toContainEqual({
      kind: "missing-rank",
      rank: 2,
      message: "rank 2 did not report (world size 4)",
    });
  });

  test("flags rank-divergence when a must-match observation disagrees across ranks", () => {
    const result = probeDistributedContract({
      worldSize: 4,
      ranks: [
        { rank: 0, steps: 100, observations: { final_loss: "abc" } },
        { rank: 1, steps: 100, observations: { final_loss: "abc" } },
        { rank: 2, steps: 100, observations: { final_loss: "DIFFERENT" } },
        { rank: 3, steps: 100, observations: { final_loss: "abc" } },
      ],
      mustMatchAcrossRanks: ["final_loss"],
    });
    expect(result.passed).toBe(false);
    expect(result.failures).toHaveLength(1);
    expect(result.failures[0]).toMatchObject({
      kind: "rank-divergence",
      key: "final_loss",
    });
    // The message must enumerate the distinct values so the caller can see
    // which ranks diverged on which value.
    expect(result.failures[0].message).toMatch(/abc/);
    expect(result.failures[0].message).toMatch(/DIFFERENT/);
  });

  test("flags wrong-step-count when a rank reports fewer steps than expected", () => {
    const result = probeDistributedContract({
      worldSize: 2,
      ranks: [
        { rank: 0, steps: 100, observations: {} },
        { rank: 1, steps: 87, observations: {} },
      ],
      expectedSteps: 100,
    });
    expect(result.passed).toBe(false);
    expect(result.failures).toContainEqual({
      kind: "wrong-step-count",
      rank: 1,
      message: "rank 1 ran 87 steps; expected 100",
    });
  });

  test("flags missing-observation when a must-match key is absent from any rank", () => {
    // PR #985 review lesson: an expectation declared without an observation
    // must fail, not silently pass. Applies here when mustMatchAcrossRanks
    // names a key that some rank never reported.
    const result = probeDistributedContract({
      worldSize: 2,
      ranks: [
        { rank: 0, steps: 100, observations: { final_loss: "abc" } },
        { rank: 1, steps: 100, observations: {} }, // missing final_loss
      ],
      mustMatchAcrossRanks: ["final_loss"],
    });
    expect(result.passed).toBe(false);
    expect(result.failures).toContainEqual({
      kind: "missing-observation",
      rank: 1,
      key: "final_loss",
      message: "rank 1 did not report observation 'final_loss'",
    });
  });

  test("flags missing-observation when worldSize is expected but unobserved", () => {
    const result = probeDistributedContract({
      // No worldSize observation supplied.
      ranks: [],
      expectedWorldSize: 4,
    });
    expect(result.passed).toBe(false);
    expect(result.failures).toContainEqual({
      kind: "missing-observation",
      message: "declared expectation on worldSize but no observation was supplied",
    });
  });

  test("does not check fields the caller declared no expectation about", () => {
    // Caller only declared mustMatchAcrossRanks; steps, world size, and
    // any other observations are not part of the contract this call signed
    // up for.
    const result = probeDistributedContract({
      worldSize: 2,
      ranks: [
        { rank: 0, steps: 50, observations: { x: "1" } },
        { rank: 1, steps: 75, observations: { x: "1" } },
      ],
      mustMatchAcrossRanks: ["x"],
    });
    expect(result.passed).toBe(true);
  });

  test("treats a single-rank report with expectedWorldSize=1 as valid", () => {
    // World size 1 is degenerate but lawful: parity-with-self holds and the
    // probe should treat the trivial case as passing.
    const result = probeDistributedContract({
      worldSize: 1,
      ranks: [{ rank: 0, steps: 10, observations: { loss: "0.1" } }],
      expectedWorldSize: 1,
      expectedSteps: 10,
      mustMatchAcrossRanks: ["loss"],
    });
    expect(result.passed).toBe(true);
  });

  test("flags duplicate-rank when the same rank id appears twice", () => {
    const result = probeDistributedContract({
      worldSize: 2,
      ranks: [
        { rank: 0, steps: 100, observations: {} },
        { rank: 0, steps: 100, observations: {} },
      ],
    });
    expect(result.passed).toBe(false);
    expect(result.failures).toContainEqual({
      kind: "duplicate-rank",
      rank: 0,
      message: "rank 0 reported more than once",
    });
  });
});
