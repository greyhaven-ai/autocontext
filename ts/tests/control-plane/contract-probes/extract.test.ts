import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, test } from "vitest";

import {
  ContractProbeSuiteSchema,
  runContractProbeSuite,
} from "../../../src/control-plane/contract-probes/index.js";
import {
  EXTRACT_HELP_TEXT,
  extractContractProbeSuite,
  HarnessTraceSchema,
  runExtract,
} from "../../../src/control-plane/contract-probes/cli/index.js";

function writeTrace(spec: unknown): string {
  const dir = mkdtempSync(join(tmpdir(), "probes-extract-"));
  const path = join(dir, "trace.json");
  writeFileSync(path, JSON.stringify(spec), "utf-8");
  return path;
}

describe("HarnessTraceSchema", () => {
  test("parses a minimal observation-only trace", () => {
    const parsed = HarnessTraceSchema.parse({
      schema_version: 1,
      observations: {
        terminal: { exitCode: 0, stdout: "ok", stderr: "" },
      },
    });
    expect(parsed.observations.terminal?.exitCode).toBe(0);
    expect(parsed.expectations).toBeUndefined();
  });

  test("parses observations + expectations and accepts regex strings", () => {
    const parsed = HarnessTraceSchema.parse({
      schema_version: 1,
      observations: {
        terminal: { exitCode: 0, stdout: "All checks passed.", stderr: "" },
        workdir: { presentFiles: ["solution.txt", "trace.log"] },
      },
      expectations: {
        terminal: { expectedExitCode: 0, requiredStdoutPatterns: ["checks passed"] },
        directory: {
          requiredFiles: ["solution.txt"],
          allowedFiles: ["solution.txt"],
          ignoredPatterns: ["^trace\\."],
        },
      },
    });
    expect(parsed.expectations?.terminal?.requiredStdoutPatterns?.[0]).toBeInstanceOf(RegExp);
    expect(parsed.expectations?.directory?.ignoredPatterns?.[0]).toBeInstanceOf(RegExp);
  });

  test("rejects unknown keys at the trace envelope", () => {
    const result = HarnessTraceSchema.safeParse({
      schema_version: 1,
      observations: { terminal: { exitCode: 0, stdout: "", stderr: "" } },
      extra: "ignored",
    });
    expect(result.success).toBe(false);
  });

  test("rejects unknown keys nested inside an observation", () => {
    const result = HarnessTraceSchema.safeParse({
      schema_version: 1,
      observations: {
        terminal: { exitCode: 0, stdout: "", stderr: "", surprise: 1 },
      },
    });
    expect(result.success).toBe(false);
  });

  test("safeParse returns { success: false } for an invalid regex pattern", () => {
    const result = HarnessTraceSchema.safeParse({
      schema_version: 1,
      observations: { terminal: { exitCode: 0, stdout: "", stderr: "" } },
      expectations: { terminal: { requiredStdoutPatterns: ["[unclosed"] } },
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(
        result.error.issues.some((i) => i.message.includes("invalid regular expression")),
      ).toBe(true);
    }
  });

  // PR #992 review (P2): expectations without their matching observations
  // were silently dropped at extraction time, so the resulting suite
  // passed vacuously. The schema now rejects orphan expectations per
  // section. The reviewer's exact repro was a trace with terminal /
  // directory / service / artifact expectations and `observations: {}`.
  test("rejects orphan terminal expectation (no observations.terminal)", () => {
    const result = HarnessTraceSchema.safeParse({
      schema_version: 1,
      observations: {},
      expectations: { terminal: { expectedExitCode: 0 } },
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      const messages = result.error.issues.map((i) => i.message).join("|");
      expect(messages).toMatch(/expectation declared without a matching observation/);
    }
  });

  test("rejects orphan directory expectation (no observations.workdir)", () => {
    const result = HarnessTraceSchema.safeParse({
      schema_version: 1,
      observations: {},
      expectations: { directory: { requiredFiles: ["x"], allowedFiles: ["x"] } },
    });
    expect(result.success).toBe(false);
  });

  test("rejects orphan services expectation (no observations.services)", () => {
    const result = HarnessTraceSchema.safeParse({
      schema_version: 1,
      observations: {},
      expectations: {
        services: { required: [{ host: "127.0.0.1", port: 8080 }] },
      },
    });
    expect(result.success).toBe(false);
  });

  test("rejects orphan artifacts expectation (no observations.artifacts)", () => {
    const result = HarnessTraceSchema.safeParse({
      schema_version: 1,
      observations: {},
      expectations: {
        artifacts: [{ path: "manifest.json", requiredJsonFields: ["name"] }],
      },
    });
    expect(result.success).toBe(false);
  });

  test("rejects orphan per-artifact expectations (observation list omits the path)", () => {
    const result = HarnessTraceSchema.safeParse({
      schema_version: 1,
      observations: { artifacts: [{ path: "a.txt", content: "" }] },
      expectations: {
        artifacts: [{ path: "b.txt", requiredSubstrings: ["x"] }],
      },
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      const messages = result.error.issues.map((i) => i.message).join("|");
      expect(messages).toMatch(/no observation with that path was recorded/);
    }
  });

  test("the reviewer's exact repro: all-expectations + empty observations is rejected", () => {
    // PR #992 review (P2): the exact shape the reviewer reproduced --
    // every expectation declared, no observations recorded -- must now
    // surface a Zod issue per orphan section instead of producing an
    // empty `probes: []` suite that vacuously passes.
    const result = HarnessTraceSchema.safeParse({
      schema_version: 1,
      observations: {},
      expectations: {
        terminal: { expectedExitCode: 0 },
        directory: { requiredFiles: ["x"], allowedFiles: ["x"] },
        services: { required: [{ host: "127.0.0.1", port: 8080 }] },
        artifacts: [{ path: "m.json", requiredJsonFields: ["name"] }],
      },
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      // One Zod issue per orphan section; not exactly four guaranteed but
      // at least four should fire.
      expect(result.error.issues.length).toBeGreaterThanOrEqual(4);
    }
  });

  // --- Slice 8: orphan-rejection coverage for cleanup / media / distributed

  test("rejects orphan cleanup expectation (no observations.cleanup)", () => {
    const result = HarnessTraceSchema.safeParse({
      schema_version: 1,
      observations: {},
      expectations: { cleanup: { maxLockfileAgeMs: 1000 } },
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      const messages = result.error.issues.map((i) => i.message).join("|");
      expect(messages).toMatch(/cleanup expectation declared without a matching observation/);
    }
  });

  test("rejects orphan media expectation (no observations.media)", () => {
    const result = HarnessTraceSchema.safeParse({
      schema_version: 1,
      observations: {},
      expectations: { media: [{ path: "rendered.png", expectedWidth: 100 }] },
    });
    expect(result.success).toBe(false);
  });

  test("rejects orphan per-media expectations (observation list omits the path)", () => {
    const result = HarnessTraceSchema.safeParse({
      schema_version: 1,
      observations: { media: [{ path: "a.png", width: 1 }] },
      expectations: { media: [{ path: "b.png", expectedWidth: 100 }] },
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      const messages = result.error.issues.map((i) => i.message).join("|");
      expect(messages).toMatch(/no observation with that path was recorded/);
    }
  });

  test("rejects orphan distributed expectation (no observations.distributed)", () => {
    const result = HarnessTraceSchema.safeParse({
      schema_version: 1,
      observations: {},
      expectations: { distributed: { expectedWorldSize: 4 } },
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      const messages = result.error.issues.map((i) => i.message).join("|");
      expect(messages).toMatch(/distributed expectation declared without a matching observation/);
    }
  });

  // PR #993 review (P2): rank-scoped expectations (`expectedSteps`,
  // `mustMatchAcrossRanks`) silently passed against `ranks: []` because the
  // slice-5 distributed probe's per-rank loop is vacuous on empty input.
  // The schema now rejects those expectations when the rank list is empty;
  // `expectedWorldSize` is still allowed since it's checked against
  // `observations.distributed.worldSize` directly.

  test("rejects rank-scoped expectations against an empty ranks list (mustMatchAcrossRanks)", () => {
    const result = HarnessTraceSchema.safeParse({
      schema_version: 1,
      observations: { distributed: { worldSize: 0, ranks: [] } },
      expectations: { distributed: { mustMatchAcrossRanks: ["loss"] } },
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      const messages = result.error.issues.map((i) => i.message).join("|");
      expect(messages).toMatch(
        /rank-scoped expectation `mustMatchAcrossRanks` requires `observations.distributed.ranks` to contain at least one report/,
      );
    }
  });

  test("rejects rank-scoped expectations against an empty ranks list (expectedSteps)", () => {
    const result = HarnessTraceSchema.safeParse({
      schema_version: 1,
      observations: { distributed: { ranks: [] } },
      expectations: { distributed: { expectedSteps: 100 } },
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      const messages = result.error.issues.map((i) => i.message).join("|");
      expect(messages).toMatch(
        /rank-scoped expectation `expectedSteps` requires `observations.distributed.ranks` to contain at least one report/,
      );
    }
  });

  test("permits non-rank-scoped expectedWorldSize even with empty ranks", () => {
    // expectedWorldSize is checked against `observations.distributed.worldSize`,
    // not against the rank list -- so it's fine to declare with no ranks.
    const result = HarnessTraceSchema.safeParse({
      schema_version: 1,
      observations: { distributed: { worldSize: 0, ranks: [] } },
      expectations: { distributed: { expectedWorldSize: 0 } },
    });
    expect(result.success).toBe(true);
  });

  // PR #993 review (P2): per-path expectations (artifacts, media) were stored
  // in a Map keyed by `path`, so a second entry for the same path silently
  // overwrote the first and dropped its assertions. Reject duplicates at
  // parse time so the caller merges entries (or sees the collision).

  test("rejects duplicate per-artifact expectations for the same path", () => {
    const result = HarnessTraceSchema.safeParse({
      schema_version: 1,
      observations: {
        artifacts: [{ path: "manifest.json", content: "{}" }],
      },
      expectations: {
        artifacts: [
          { path: "manifest.json", requiredJsonFields: ["name"] },
          { path: "manifest.json", requiredJsonFields: ["version"] },
        ],
      },
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      const messages = result.error.issues.map((i) => i.message).join("|");
      expect(messages).toMatch(/duplicate per-artifact expectation/);
    }
  });

  test("rejects duplicate per-media expectations for the same path", () => {
    // Reviewer's exact repro: two `rendered.png` entries, first declares
    // expectedWidth, second declares expectedHeight; the previous extractor
    // silently dropped the width assertion.
    const result = HarnessTraceSchema.safeParse({
      schema_version: 1,
      observations: { media: [{ path: "rendered.png", width: 100, height: 50 }] },
      expectations: {
        media: [
          { path: "rendered.png", expectedWidth: 100 },
          { path: "rendered.png", expectedHeight: 50 },
        ],
      },
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      const messages = result.error.issues.map((i) => i.message).join("|");
      expect(messages).toMatch(/duplicate per-media expectation/);
    }
  });
});

describe("extractContractProbeSuite", () => {
  test("an observations-only trace produces probes with no declared expectations", () => {
    const trace = HarnessTraceSchema.parse({
      schema_version: 1,
      observations: { terminal: { exitCode: 0, stdout: "ok", stderr: "" } },
    });
    const suite = extractContractProbeSuite(trace);
    expect(suite.probes.map((p) => p.kind)).toEqual(["terminal"]);
    // With no terminal expectation, the terminal probe's `expectedExitCode`
    // defaults to 0 -- so exitCode=0 still passes.
    const result = runContractProbeSuite(suite);
    expect(result.passed).toBe(true);
  });

  test("observation-only workdir fails by default (no allowlist declared)", () => {
    // The directory probe's contract is "files must be on the allowlist".
    // A trace with workdir observations but no directory expectations
    // produces a probe with empty `allowedFiles`, so every present file is
    // unexpected. This is intended: it prompts the operator to declare an
    // allowlist rather than silently passing.
    const trace = HarnessTraceSchema.parse({
      schema_version: 1,
      observations: { workdir: { presentFiles: ["a.txt"] } },
    });
    const suite = extractContractProbeSuite(trace);
    const result = runContractProbeSuite(suite);
    expect(result.passed).toBe(false);
    const probe = result.results[0];
    if (probe.kind !== "directory") {
      throw new Error("expected directory");
    }
    expect(probe.failures[0].kind).toBe("unexpected-file");
  });

  test("joins terminal observation + expectation into a single probe", () => {
    const trace = HarnessTraceSchema.parse({
      schema_version: 1,
      observations: { terminal: { exitCode: 0, stdout: "done", stderr: "" } },
      expectations: { terminal: { expectedExitCode: 0, requiredStdoutPatterns: ["done"] } },
    });
    const suite = extractContractProbeSuite(trace);
    expect(suite.probes).toHaveLength(1);
    const probe = suite.probes[0];
    if (probe.kind !== "terminal") {
      throw new Error("expected terminal");
    }
    expect(probe.inputs.expectedExitCode).toBe(0);
    expect(runContractProbeSuite(suite).passed).toBe(true);
  });

  test("joins workdir observation + directory expectation; ignoredPatterns flow through", () => {
    const trace = HarnessTraceSchema.parse({
      schema_version: 1,
      observations: {
        workdir: { presentFiles: ["solution.txt", "trace.log"] },
      },
      expectations: {
        directory: {
          requiredFiles: ["solution.txt"],
          allowedFiles: ["solution.txt"],
          ignoredPatterns: ["^trace\\."],
        },
      },
    });
    const suite = extractContractProbeSuite(trace);
    const result = runContractProbeSuite(suite);
    expect(result.passed).toBe(true);
  });

  test("flags unexpected files when the directory expectation omits the ignore", () => {
    const trace = HarnessTraceSchema.parse({
      schema_version: 1,
      observations: { workdir: { presentFiles: ["solution.txt", "trace.log"] } },
      expectations: {
        directory: { requiredFiles: ["solution.txt"], allowedFiles: ["solution.txt"] },
      },
    });
    const suite = extractContractProbeSuite(trace);
    const result = runContractProbeSuite(suite);
    expect(result.passed).toBe(false);
    const probe = result.results[0];
    if (probe.kind !== "directory") {
      throw new Error("expected directory");
    }
    expect(probe.failures[0].kind).toBe("unexpected-file");
    expect(probe.failures[0].path).toBe("trace.log");
  });

  test("matches per-artifact expectations by path", () => {
    const trace = HarnessTraceSchema.parse({
      schema_version: 1,
      observations: {
        artifacts: [
          { path: "manifest.json", content: JSON.stringify({ name: "x" }) },
          { path: "notes.txt", content: "hello\n" },
        ],
      },
      expectations: {
        artifacts: [
          { path: "manifest.json", requiredJsonFields: ["name", "version"] },
          { path: "notes.txt", requiredSubstrings: ["hello"] },
        ],
      },
    });
    const suite = extractContractProbeSuite(trace);
    expect(suite.probes).toHaveLength(2);
    const result = runContractProbeSuite(suite);
    // manifest.json is missing "version" -> fail; notes.txt has "hello" -> pass.
    expect(result.passed).toBe(false);
    const manifestProbe = result.results[0];
    if (manifestProbe.kind !== "artifact") {
      throw new Error("expected artifact");
    }
    expect(manifestProbe.failures[0].kind).toBe("missing-json-field");
    expect(manifestProbe.failures[0].path).toBe("version");
  });

  test("emits an artifact probe with no expectations when an observation has no matching expectations entry", () => {
    // An observation without an expectations entry IS permitted (the
    // operator chose not to declare any requirements about it); the
    // resulting probe records the artifact's existence and content with
    // no substring / line-ending / JSON-field assertions. The reverse
    // case (expectation referencing an unobserved path) is rejected --
    // see "rejects orphan per-artifact expectations".
    const trace = HarnessTraceSchema.parse({
      schema_version: 1,
      observations: {
        artifacts: [
          { path: "extra.txt", content: "" },
          { path: "other.txt", content: "x" },
        ],
      },
      expectations: {
        artifacts: [{ path: "other.txt", requiredSubstrings: ["x"] }],
      },
    });
    const suite = extractContractProbeSuite(trace);
    expect(suite.probes).toHaveLength(2);
    const extraProbe = suite.probes[0];
    if (extraProbe.kind !== "artifact") {
      throw new Error("expected artifact");
    }
    expect(extraProbe.inputs.path).toBe("extra.txt");
    expect(extraProbe.inputs.requiredSubstrings).toBeUndefined();
  });

  test("end-to-end: extracted suite passes ContractProbeSuiteSchema and runs through runner", () => {
    const trace = HarnessTraceSchema.parse({
      schema_version: 1,
      label: "smoke-run-2026-05-22",
      observations: {
        terminal: { exitCode: 0, stdout: "All checks passed.", stderr: "" },
        workdir: { presentFiles: ["solution.txt"] },
        services: [{ host: "127.0.0.1", port: 8080, protocol: "tcp" }],
      },
      expectations: {
        terminal: { expectedExitCode: 0, requiredStdoutPatterns: ["checks passed"] },
        directory: { requiredFiles: ["solution.txt"], allowedFiles: ["solution.txt"] },
        services: { required: [{ host: "127.0.0.1", port: 8080, protocol: "tcp" }] },
      },
    });
    const suite = extractContractProbeSuite(trace);
    // Round-trip the suite through its own schema to confirm the extractor
    // produces a runnable, fully-validated ContractProbeSuite (not just a
    // structurally-similar TypeScript value).
    const reparsed = ContractProbeSuiteSchema.parse(
      JSON.parse(
        JSON.stringify(suite, (_k, v) =>
          v instanceof RegExp ? { source: v.source, flags: v.flags } : v,
        ),
      ),
    );
    const result = runContractProbeSuite(reparsed);
    expect(result.passed).toBe(true);
    expect(result.results.every((r) => r.label === "smoke-run-2026-05-22")).toBe(true);
  });

  // --- Slice 8: cleanup / media / distributed extractors ------------------

  test("cleanup: joins entries + age-threshold expectation into a cleanup probe", () => {
    const trace = HarnessTraceSchema.parse({
      schema_version: 1,
      observations: {
        cleanup: {
          entries: [
            { path: "solution.txt" },
            { path: "stale.lock", mtime: "2026-05-21T10:00:00Z" },
          ],
        },
      },
      expectations: {
        cleanup: { now: "2026-05-21T12:00:00Z", maxLockfileAgeMs: 5 * 60 * 1000 },
      },
    });
    const suite = extractContractProbeSuite(trace);
    expect(suite.probes).toHaveLength(1);
    const probe = suite.probes[0];
    if (probe.kind !== "cleanup") {
      throw new Error("expected cleanup");
    }
    const result = runContractProbeSuite(suite);
    expect(result.passed).toBe(false);
    if (result.results[0].kind !== "cleanup") {
      throw new Error("expected cleanup");
    }
    expect(result.results[0].failures[0].kind).toBe("stale-lockfile");
  });

  test("cleanup: observation-only trace produces a cleanup probe with default policy", () => {
    const trace = HarnessTraceSchema.parse({
      schema_version: 1,
      observations: { cleanup: { entries: [{ path: ".DS_Store" }] } },
    });
    const suite = extractContractProbeSuite(trace);
    const result = runContractProbeSuite(suite);
    expect(result.passed).toBe(false);
    if (result.results[0].kind !== "cleanup") {
      throw new Error("expected cleanup");
    }
    expect(result.results[0].failures[0].kind).toBe("stray-sidecar");
  });

  test("media: matches per-media expectations by path", () => {
    const trace = HarnessTraceSchema.parse({
      schema_version: 1,
      observations: {
        media: [
          {
            path: "rendered.png",
            headerBytes: [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a],
            width: 100,
            height: 50,
          },
        ],
      },
      expectations: {
        media: [
          {
            path: "rendered.png",
            expectedMagicBytes: [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a],
            expectedWidth: 128,
            expectedHeight: 64,
          },
        ],
      },
    });
    const suite = extractContractProbeSuite(trace);
    expect(suite.probes).toHaveLength(1);
    const result = runContractProbeSuite(suite);
    expect(result.passed).toBe(false);
    if (result.results[0].kind !== "media") {
      throw new Error("expected media");
    }
    // PNG header matches, but width and height disagree -> two
    // wrong-dimensions failures.
    expect(result.results[0].failures.map((f) => f.kind).sort()).toEqual([
      "wrong-dimensions",
      "wrong-dimensions",
    ]);
  });

  test("media: observation-without-matching-expectation yields a no-op media probe", () => {
    const trace = HarnessTraceSchema.parse({
      schema_version: 1,
      observations: {
        media: [
          { path: "extra.png", width: 100 },
          { path: "other.png", width: 200 },
        ],
      },
      expectations: {
        media: [{ path: "other.png", expectedWidth: 200 }],
      },
    });
    const suite = extractContractProbeSuite(trace);
    expect(suite.probes).toHaveLength(2);
    const result = runContractProbeSuite(suite);
    // extra.png: no expectations -> passes; other.png: width matches ->
    // passes; aggregate passes.
    expect(result.passed).toBe(true);
  });

  test("distributed: joins ranks + cross-rank expectation into a distributed probe", () => {
    const trace = HarnessTraceSchema.parse({
      schema_version: 1,
      observations: {
        distributed: {
          worldSize: 2,
          ranks: [
            { rank: 0, steps: 100, observations: { final_loss: "abc" } },
            { rank: 1, steps: 100, observations: { final_loss: "DIFFERENT" } },
          ],
        },
      },
      expectations: {
        distributed: { expectedWorldSize: 2, mustMatchAcrossRanks: ["final_loss"] },
      },
    });
    const suite = extractContractProbeSuite(trace);
    expect(suite.probes).toHaveLength(1);
    const result = runContractProbeSuite(suite);
    expect(result.passed).toBe(false);
    if (result.results[0].kind !== "distributed") {
      throw new Error("expected distributed");
    }
    expect(result.results[0].failures[0].kind).toBe("rank-divergence");
  });

  test("distributed: observation-only trace passes without cross-rank assertions", () => {
    const trace = HarnessTraceSchema.parse({
      schema_version: 1,
      observations: {
        distributed: {
          worldSize: 1,
          ranks: [{ rank: 0, steps: 10 }],
        },
      },
    });
    const suite = extractContractProbeSuite(trace);
    expect(runContractProbeSuite(suite).passed).toBe(true);
  });

  test("end-to-end: a seven-probe trace round-trips through extract + runner", () => {
    const trace = HarnessTraceSchema.parse({
      schema_version: 1,
      observations: {
        terminal: { exitCode: 0, stdout: "ok", stderr: "" },
        workdir: { presentFiles: ["a.txt"] },
        services: [{ host: "127.0.0.1", port: 8080, protocol: "tcp" }],
        artifacts: [{ path: "manifest.json", content: '{"name":"x"}' }],
        cleanup: { entries: [{ path: "a.txt" }] },
        media: [{ path: "rendered.png", width: 100, height: 50 }],
        distributed: {
          worldSize: 1,
          ranks: [{ rank: 0, observations: { loss: "0.1" } }],
        },
      },
      expectations: {
        terminal: { expectedExitCode: 0 },
        directory: { requiredFiles: ["a.txt"], allowedFiles: ["a.txt"] },
        services: { required: [{ host: "127.0.0.1", port: 8080, protocol: "tcp" }] },
        artifacts: [{ path: "manifest.json", requiredJsonFields: ["name"] }],
        cleanup: {},
        media: [{ path: "rendered.png", expectedWidth: 100, expectedHeight: 50 }],
        distributed: { expectedWorldSize: 1, mustMatchAcrossRanks: ["loss"] },
      },
    });
    const suite = extractContractProbeSuite(trace);
    expect(suite.probes.map((p) => p.kind)).toEqual([
      "terminal",
      "directory",
      "service",
      "artifact",
      "cleanup",
      "media",
      "distributed",
    ]);
    const result = runContractProbeSuite(suite);
    expect(result.passed).toBe(true);
  });
});

describe("runExtract", () => {
  test("--help prints subcommand help and exits 0", async () => {
    const result = await runExtract(["--help"]);
    expect(result.exitCode).toBe(0);
    expect(result.stdout).toBe(EXTRACT_HELP_TEXT);
  });

  test("missing --trace exits 1 with a helpful message", async () => {
    const result = await runExtract([]);
    expect(result.exitCode).toBe(1);
    expect(result.stderr).toContain("--trace <path> is required");
  });

  test("non-existent trace path exits 1", async () => {
    const result = await runExtract(["--trace", "/no/such/file.json"]);
    expect(result.exitCode).toBe(1);
    expect(result.stderr).toContain("failed to read trace");
  });

  test("malformed JSON trace exits 1", async () => {
    const dir = mkdtempSync(join(tmpdir(), "probes-extract-"));
    const path = join(dir, "bad.json");
    writeFileSync(path, "{not valid", "utf-8");
    const result = await runExtract(["--trace", path]);
    expect(result.exitCode).toBe(1);
    expect(result.stderr).toContain("invalid JSON");
  });

  test("schema-invalid trace exits 1 with Zod issues", async () => {
    const path = writeTrace({
      schema_version: 1,
      observations: { terminal: { exitCode: 0, stdout: "", stderr: "", typo: 1 } },
    });
    const result = await runExtract(["--trace", path]);
    expect(result.exitCode).toBe(1);
    expect(result.stderr).toContain("validation failed");
  });

  test("emits the suite to stdout when --output is not supplied", async () => {
    const path = writeTrace({
      schema_version: 1,
      observations: { terminal: { exitCode: 0, stdout: "ok", stderr: "" } },
      expectations: { terminal: { expectedExitCode: 0 } },
    });
    const result = await runExtract(["--trace", path]);
    expect(result.exitCode).toBe(0);
    const suite = JSON.parse(result.stdout) as {
      schema_version: number;
      probes: { kind: string }[];
    };
    expect(suite.schema_version).toBe(1);
    expect(suite.probes[0].kind).toBe("terminal");
  });

  test("writes the suite to --output when supplied; report on stdout", async () => {
    const path = writeTrace({
      schema_version: 1,
      observations: { terminal: { exitCode: 0, stdout: "ok", stderr: "" } },
    });
    const dir = mkdtempSync(join(tmpdir(), "probes-extract-out-"));
    const outPath = join(dir, "nested", "suite.json");
    const result = await runExtract(["--trace", path, "--output", outPath]);
    expect(result.exitCode).toBe(0);
    expect(result.stdout).toContain(`wrote suite to ${outPath}`);
    const written = JSON.parse(readFileSync(outPath, "utf-8")) as {
      schema_version: number;
      probes: unknown[];
    };
    expect(written.schema_version).toBe(1);
  });

  test("end-to-end: emitted suite passes the slice-5 suite-runner schema", async () => {
    // The serialized suite must be valid input for `loadContractProbeSuite`
    // / `ContractProbeSuiteSchema.parse`. RegExp values are serialized as
    // `{ source, flags }` objects so the runner schema can re-parse them.
    const path = writeTrace({
      schema_version: 1,
      observations: { workdir: { presentFiles: ["solution.txt"] } },
      expectations: {
        directory: {
          requiredFiles: ["solution.txt"],
          allowedFiles: ["solution.txt"],
          ignoredPatterns: ["^trace\\."],
        },
      },
    });
    const result = await runExtract(["--trace", path]);
    expect(result.exitCode).toBe(0);
    const raw = JSON.parse(result.stdout) as unknown;
    const suite = ContractProbeSuiteSchema.parse(raw);
    const probe = suite.probes[0];
    if (probe.kind !== "directory") {
      throw new Error("expected directory");
    }
    expect(probe.inputs.ignoredPatterns?.[0]).toBeInstanceOf(RegExp);
    expect(runContractProbeSuite(suite).passed).toBe(true);
  });
});
