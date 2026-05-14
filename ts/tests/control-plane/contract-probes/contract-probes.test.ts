import { describe, expect, test } from "vitest";
import {
  probeArtifactContract,
  probeDirectoryContract,
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
