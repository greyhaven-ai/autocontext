import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, test } from "vitest";

import {
  ContractProbeSuiteSchema,
  loadContractProbeSuite,
  runContractProbeSuite,
} from "../../../src/control-plane/contract-probes/index.js";
import type { ContractProbeSuite } from "../../../src/control-plane/contract-probes/index.js";

describe("ContractProbeSuiteSchema", () => {
  test("parses a minimal empty suite", () => {
    const parsed = ContractProbeSuiteSchema.parse({ schema_version: 1, probes: [] });
    expect(parsed.probes).toEqual([]);
  });

  test("rejects an unknown probe kind", () => {
    expect(() =>
      ContractProbeSuiteSchema.parse({
        schema_version: 1,
        probes: [{ kind: "nonsense", inputs: {} }],
      }),
    ).toThrow();
  });

  test("rejects schema_version != 1", () => {
    expect(() => ContractProbeSuiteSchema.parse({ schema_version: 99, probes: [] })).toThrow();
  });

  test("transforms string ignoredPatterns into RegExp", () => {
    const parsed = ContractProbeSuiteSchema.parse({
      schema_version: 1,
      probes: [
        {
          kind: "directory",
          inputs: {
            presentFiles: ["a"],
            requiredFiles: ["a"],
            allowedFiles: ["a"],
            ignoredPatterns: ["^trace\\."],
          },
        },
      ],
    });
    const invocation = parsed.probes[0];
    expect(invocation.kind).toBe("directory");
    if (invocation.kind !== "directory") {
      return;
    }
    expect(invocation.inputs.ignoredPatterns?.[0]).toBeInstanceOf(RegExp);
    expect(invocation.inputs.ignoredPatterns?.[0].test("trace.log")).toBe(true);
  });

  test("transforms ISO-8601 strings to Date for cleanup mtime / now", () => {
    const parsed = ContractProbeSuiteSchema.parse({
      schema_version: 1,
      probes: [
        {
          kind: "cleanup",
          inputs: {
            entries: [{ path: "stale.lock", mtime: "2026-05-21T11:00:00Z" }],
            now: "2026-05-21T12:00:00Z",
            maxLockfileAgeMs: 5 * 60 * 1000,
          },
        },
      ],
    });
    const invocation = parsed.probes[0];
    expect(invocation.kind).toBe("cleanup");
    if (invocation.kind !== "cleanup") {
      return;
    }
    expect(invocation.inputs.now).toBeInstanceOf(Date);
    expect(invocation.inputs.entries[0].mtime).toBeInstanceOf(Date);
  });

  test("rejects malformed ISO-8601 strings", () => {
    expect(() =>
      ContractProbeSuiteSchema.parse({
        schema_version: 1,
        probes: [
          {
            kind: "cleanup",
            inputs: {
              entries: [{ path: "x.lock", mtime: "not-a-date" }],
            },
          },
        ],
      }),
    ).toThrow(/invalid ISO-8601 date/);
  });

  // PR #990 review (P2): the previous version used z.object without
  // .strict(), so a typo like `requiredStdoutPattern` (singular) silently
  // disappeared and the probe ran without the expectation. The whole
  // contract-probe library is supposed to catch contract bugs; eating
  // typos in the contract specification itself is exactly the wrong
  // behavior. Every schema now rejects unknown keys.

  test("rejects unknown keys on probe inputs (typo in field name)", () => {
    const parsed = ContractProbeSuiteSchema.safeParse({
      schema_version: 1,
      probes: [
        {
          kind: "terminal",
          inputs: {
            exitCode: 0,
            stdout: "ok",
            stderr: "",
            requiredStdoutPattern: ["expected"], // missing trailing `s`
          },
        },
      ],
    });
    expect(parsed.success).toBe(false);
    if (!parsed.success) {
      const messages = parsed.error.issues.map((i) => i.message).join("|");
      expect(messages).toMatch(/requiredStdoutPattern/);
    }
  });

  test("rejects unknown keys on the invocation envelope", () => {
    const parsed = ContractProbeSuiteSchema.safeParse({
      schema_version: 1,
      probes: [
        {
          kind: "directory",
          inputs2: {}, // typo
          inputs: { presentFiles: [], requiredFiles: [], allowedFiles: [] },
        },
      ],
    });
    expect(parsed.success).toBe(false);
  });

  test("rejects unknown keys on the suite envelope", () => {
    const parsed = ContractProbeSuiteSchema.safeParse({
      schema_version: 1,
      probes: [],
      extra_field: "ignored", // unknown top-level key
    });
    expect(parsed.success).toBe(false);
  });

  test("rejects unknown keys nested in service-endpoint observations", () => {
    const parsed = ContractProbeSuiteSchema.safeParse({
      schema_version: 1,
      probes: [
        {
          kind: "service",
          inputs: {
            observed: [
              { host: "127.0.0.1", port: 8080, secure: true }, // unknown `secure`
            ],
            required: [],
          },
        },
      ],
    });
    expect(parsed.success).toBe(false);
  });

  // PR #990 review (P2): the previous RegExpJson / DateJson transforms threw
  // raw SyntaxError / Error from inside the transform callback. That broke
  // the safeParse contract: callers should be able to use `safeParse` to
  // get `{ success: false, error }` for any invalid input. Now both
  // transforms report via ctx.addIssue and return z.NEVER, so safeParse
  // returns `{ success: false }` cleanly.

  test("safeParse returns { success: false } for an invalid regex pattern", () => {
    const parsed = ContractProbeSuiteSchema.safeParse({
      schema_version: 1,
      probes: [
        {
          kind: "directory",
          inputs: {
            presentFiles: [],
            requiredFiles: [],
            allowedFiles: [],
            ignoredPatterns: ["[unclosed"], // SyntaxError on new RegExp(...)
          },
        },
      ],
    });
    expect(parsed.success).toBe(false);
    if (!parsed.success) {
      expect(
        parsed.error.issues.some((i) => i.message.includes("invalid regular expression")),
      ).toBe(true);
    }
  });

  test("safeParse returns { success: false } for a malformed ISO-8601 date", () => {
    const parsed = ContractProbeSuiteSchema.safeParse({
      schema_version: 1,
      probes: [
        {
          kind: "cleanup",
          inputs: {
            entries: [{ path: "x.lock", mtime: "not-a-date" }],
          },
        },
      ],
    });
    expect(parsed.success).toBe(false);
    if (!parsed.success) {
      expect(parsed.error.issues.some((i) => i.message.includes("invalid ISO-8601 date"))).toBe(
        true,
      );
    }
  });
});

describe("runContractProbeSuite", () => {
  test("an empty suite passes", () => {
    const result = runContractProbeSuite({ schema_version: 1, probes: [] });
    expect(result.passed).toBe(true);
    expect(result.results).toEqual([]);
  });

  test("dispatches every probe kind through the matching probe (all pass)", () => {
    const suite: ContractProbeSuite = ContractProbeSuiteSchema.parse({
      schema_version: 1,
      probes: [
        {
          kind: "directory",
          label: "final-workdir",
          inputs: {
            presentFiles: ["solution.txt"],
            requiredFiles: ["solution.txt"],
            allowedFiles: ["solution.txt"],
          },
        },
        {
          kind: "terminal",
          inputs: { exitCode: 0, stdout: "ok", stderr: "", expectedExitCode: 0 },
        },
        {
          kind: "service",
          inputs: {
            observed: [{ host: "127.0.0.1", port: 8080, protocol: "tcp" }],
            required: [{ host: "127.0.0.1", port: 8080, protocol: "tcp" }],
          },
        },
        {
          kind: "artifact",
          inputs: {
            path: "config.txt",
            content: "key=value\n",
            expectedLineEnding: "lf",
            requiredSubstrings: ["key=value"],
          },
        },
        {
          kind: "cleanup",
          inputs: { entries: [{ path: "solution.txt" }] },
        },
        {
          kind: "media",
          inputs: { path: "rendered.png" },
        },
        {
          kind: "distributed",
          inputs: {
            ranks: [{ rank: 0, steps: 10, observations: { loss: "0.1" } }],
            worldSize: 1,
            expectedWorldSize: 1,
            expectedSteps: 10,
            mustMatchAcrossRanks: ["loss"],
          },
        },
      ],
    });
    const result = runContractProbeSuite(suite);
    expect(result.passed).toBe(true);
    expect(result.results.map((r) => r.kind)).toEqual([
      "directory",
      "terminal",
      "service",
      "artifact",
      "cleanup",
      "media",
      "distributed",
    ]);
    expect(result.results[0].label).toBe("final-workdir");
    expect(result.results.every((r) => r.passed)).toBe(true);
  });

  test("suite passed is the AND of per-probe passes", () => {
    const suite = ContractProbeSuiteSchema.parse({
      schema_version: 1,
      probes: [
        {
          kind: "terminal",
          label: "ok",
          inputs: { exitCode: 0, stdout: "", stderr: "" },
        },
        {
          kind: "terminal",
          label: "bad",
          inputs: { exitCode: 1, stdout: "", stderr: "" },
        },
      ],
    });
    const result = runContractProbeSuite(suite);
    expect(result.passed).toBe(false);
    expect(result.results[0].passed).toBe(true);
    expect(result.results[1].passed).toBe(false);
    expect(result.results[1].failures[0].kind).toBe("unexpected-exit-code");
  });

  test("failure entries carry through the kind + label so callers can attribute the report", () => {
    const suite = ContractProbeSuiteSchema.parse({
      schema_version: 1,
      probes: [
        {
          kind: "cleanup",
          label: "after-build",
          inputs: { entries: [{ path: ".DS_Store" }] },
        },
      ],
    });
    const result = runContractProbeSuite(suite);
    expect(result.passed).toBe(false);
    expect(result.results[0].kind).toBe("cleanup");
    expect(result.results[0].label).toBe("after-build");
    expect(result.results[0].failures[0].kind).toBe("stray-sidecar");
  });

  // PR #990 review (P3): ContractProbeRunResult is a discriminated union
  // over `kind`, so callers can switch and access each probe's specific
  // failure fields (path, rank, key, endpoint) without casting.
  test("run-result discriminated union preserves per-probe failure fields", () => {
    const suite = ContractProbeSuiteSchema.parse({
      schema_version: 1,
      probes: [
        {
          kind: "directory",
          inputs: {
            presentFiles: ["surprise.tmp"],
            requiredFiles: [],
            allowedFiles: [],
          },
        },
        {
          kind: "distributed",
          inputs: {
            ranks: [
              { rank: 0, observations: { final_loss: "abc" } },
              { rank: 1, observations: { final_loss: "DIFFERENT" } },
            ],
            worldSize: 2,
            mustMatchAcrossRanks: ["final_loss"],
          },
        },
        {
          kind: "service",
          inputs: {
            observed: [{ host: "0.0.0.0", port: 8080, protocol: "tcp" }],
            required: [{ host: "127.0.0.1", port: 8080, protocol: "tcp" }],
          },
        },
      ],
    });
    const result = runContractProbeSuite(suite);

    const directoryResult = result.results[0];
    if (directoryResult.kind !== "directory") {
      throw new Error("expected directory result");
    }
    // Per-probe field `path` is accessible without casting.
    expect(directoryResult.failures[0].path).toBe("surprise.tmp");

    const distributedResult = result.results[1];
    if (distributedResult.kind !== "distributed") {
      throw new Error("expected distributed result");
    }
    // Per-probe field `key` (and optional `rank`) are accessible without
    // casting after narrowing on `kind`.
    expect(distributedResult.failures[0].key).toBe("final_loss");

    const serviceResult = result.results[2];
    if (serviceResult.kind !== "service") {
      throw new Error("expected service result");
    }
    // Per-probe field `endpoint` is accessible without casting.
    expect(serviceResult.failures[0].endpoint.port).toBe(8080);
  });
});

describe("loadContractProbeSuite", () => {
  test("reads + parses a JSON file from disk", () => {
    const dir = mkdtempSync(join(tmpdir(), "probe-suite-"));
    const path = join(dir, "suite.json");
    writeFileSync(
      path,
      JSON.stringify({
        schema_version: 1,
        probes: [
          {
            kind: "directory",
            label: "final",
            inputs: {
              presentFiles: ["a.txt"],
              requiredFiles: ["a.txt"],
              allowedFiles: ["a.txt"],
            },
          },
        ],
      }),
      "utf-8",
    );
    const suite = loadContractProbeSuite(path);
    expect(suite.probes).toHaveLength(1);
    expect(suite.probes[0].kind).toBe("directory");
    const result = runContractProbeSuite(suite);
    expect(result.passed).toBe(true);
  });

  test("throws when the file does not exist", () => {
    expect(() => loadContractProbeSuite("/no/such/file.json")).toThrow();
  });

  test("throws when the file is malformed JSON", () => {
    const dir = mkdtempSync(join(tmpdir(), "probe-suite-"));
    const path = join(dir, "bad.json");
    writeFileSync(path, "{not valid", "utf-8");
    expect(() => loadContractProbeSuite(path)).toThrow();
  });
});
