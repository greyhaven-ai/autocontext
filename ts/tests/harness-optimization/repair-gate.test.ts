import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, test, expect } from "vitest";
import { EventStreamEmitter } from "../../src/loop/events.js";
import { validateRepairResult } from "../../src/harness-optimization/contract/validators.js";
import type { ArtifactContractProbeInputs } from "../../src/control-plane/contract-probes/index.js";
import {
  DEFAULT_REPAIRS,
  RepairGate,
  finishGuardStep,
  repairArtifactLandingStep,
  repairGateActiveFor,
  repairToolCallJsonStep,
  type RepairContext,
} from "../../src/harness-optimization/repair-gate.js";

// ---------------------------------------------------------------------------
// repairGateActiveFor: the opt-in truth table (parity with Python)
// ---------------------------------------------------------------------------

describe("repairGateActiveFor", () => {
  test("active when enabled and scenario allowlisted", () => {
    const config = { enabled: true, scenarios: "grid_ctf, othello" };
    expect(repairGateActiveFor(config, "grid_ctf")).toBe(true);
    expect(repairGateActiveFor(config, "othello")).toBe(true);
  });

  test("inactive when enabled but scenario not allowlisted", () => {
    expect(repairGateActiveFor({ enabled: true, scenarios: "grid_ctf" }, "othello")).toBe(false);
  });

  test("inactive when disabled even if scenario listed", () => {
    expect(repairGateActiveFor({ enabled: false, scenarios: "grid_ctf" }, "grid_ctf")).toBe(false);
  });

  test("inactive when allowlist empty", () => {
    expect(repairGateActiveFor({ enabled: true, scenarios: "" }, "grid_ctf")).toBe(false);
  });

  test("accepts a string-array allowlist", () => {
    expect(repairGateActiveFor({ enabled: true, scenarios: ["grid_ctf"] }, "grid_ctf")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// RepairGate.run: events + schema-valid payloads
// ---------------------------------------------------------------------------

interface Captured {
  event: string;
  payload: Record<string, unknown>;
}

function gate(
  steps?: readonly ((ctx: RepairContext) => ReturnType<typeof repairToolCallJsonStep>)[],
): {
  gate: RepairGate;
  captured: Captured[];
} {
  const dir = mkdtempSync(join(tmpdir(), "repair-gate-"));
  const emitter = new EventStreamEmitter(join(dir, "events.ndjson"));
  const captured: Captured[] = [];
  emitter.subscribe((event, payload) => captured.push({ event, payload }));
  return { gate: new RepairGate(emitter, steps ?? DEFAULT_REPAIRS), captured };
}

describe("RepairGate.run", () => {
  test("emits repair_applied with a schema-valid payload", () => {
    const { gate: g, captured } = gate([repairToolCallJsonStep]);
    const ctx: RepairContext = { toolCallJson: '{"a": 1,}' }; // trailing comma -> repairable

    const results = g.run("grid_ctf", ctx);

    expect(results).toHaveLength(1);
    expect(results[0].status).toBe("applied");
    expect(ctx.repairedToolCallJson).toBe('{"a": 1}');
    expect(captured).toHaveLength(1);
    expect(captured[0].event).toBe("repair_applied");
    expect(captured[0].payload.scenario).toBe("grid_ctf");
    expect(validateRepairResult(captured[0].payload.result).valid).toBe(true);
  });

  test("emits repair_skipped for an ambiguous input", () => {
    const { gate: g, captured } = gate([repairToolCallJsonStep]);
    const ctx: RepairContext = { toolCallJson: "{{{{ not json" };

    const results = g.run("grid_ctf", ctx);

    expect(results[0].status).toBe("skipped");
    expect(captured.map((c) => c.event)).toEqual(["repair_skipped"]);
    expect(captured[0].payload.scenario).toBe("grid_ctf");
    expect(validateRepairResult(captured[0].payload.result).valid).toBe(true);
  });

  test("emits repair_skipped (not_applicable) for absent input", () => {
    const { gate: g, captured } = gate([finishGuardStep]);
    const ctx: RepairContext = {}; // no finish claim present

    const results = g.run("grid_ctf", ctx);

    expect(results[0].status).toBe("not_applicable");
    // a normal skipped event for a both-languages repair is implemented/implemented, not a parity gap.
    expect(results[0].parity).toEqual({
      python: "implemented",
      typescript: "implemented",
      schema_hash: "",
    });
    expect(captured.map((c) => c.event)).toEqual(["repair_skipped"]);
    expect(validateRepairResult(captured[0].payload.result).valid).toBe(true);
  });

  test("applies artifact relocation and emits repair_applied", () => {
    const expected: ArtifactContractProbeInputs = {
      path: "out/report.md",
      content: "",
      requiredSubstrings: ["SUMMARY"],
    };
    const { gate: g, captured } = gate([repairArtifactLandingStep]);
    const ctx: RepairContext = {
      artifactExpected: expected,
      artifactProduced: { "tmp/report.md": "SUMMARY: all good" },
    };

    const results = g.run("grid_ctf", ctx);

    expect(results[0].status).toBe("applied");
    expect(ctx.relocationTarget).toBe("tmp/report.md");
    expect(captured.map((c) => c.event)).toEqual(["repair_applied"]);
    expect(validateRepairResult(captured[0].payload.result).valid).toBe(true);
  });

  test("emits one event per enabled repair", () => {
    const { gate: g, captured } = gate(); // DEFAULT_REPAIRS (three repairs)
    const ctx: RepairContext = { toolCallJson: '{"a": 1}' };

    const results = g.run("grid_ctf", ctx);

    expect(results).toHaveLength(DEFAULT_REPAIRS.length);
    expect(DEFAULT_REPAIRS.length).toBe(3);
    expect(captured).toHaveLength(3);
    for (const c of captured) {
      expect(c.payload.scenario).toBe("grid_ctf");
      expect(validateRepairResult(c.payload.result).valid).toBe(true);
    }
  });
});
