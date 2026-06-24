import { describe, expect, it } from "vitest";

import {
  applyModelStateTransition,
  buildPromotionEvent,
  createModelRecord,
  generateModelId,
  listModelRecordsForScenario,
  resolveActiveModelRecord,
} from "../src/training/promotion-registry-workflow.js";
import type { ModelRecord } from "../src/training/promotion-types.js";

function makeRecord(overrides?: Partial<ModelRecord>): ModelRecord {
  return {
    artifactId: overrides?.artifactId ?? "model_1",
    scenario: overrides?.scenario ?? "grid_ctf",
    family: overrides?.family ?? "game",
    backend: overrides?.backend ?? "cuda",
    checkpointDir: overrides?.checkpointDir ?? "/tmp/checkpoint",
    activationState: overrides?.activationState ?? "candidate",
    trainingScale: overrides?.trainingScale,
    promotionHistory: overrides?.promotionHistory ?? [],
    registeredAt: overrides?.registeredAt ?? "2026-03-27T10:00:00Z",
  };
}

describe("promotion registry workflow", () => {
  it("creates model ids, records, and promotion events with expected defaults", () => {
    expect(generateModelId()).toMatch(/^model_/);

    const record = createModelRecord({
      scenario: "grid_ctf",
      family: "game",
      backend: "cuda",
      checkpointDir: "/tmp/checkpoint",
    });
    expect(record.activationState).toBe("candidate");
    expect(record.promotionHistory).toEqual([]);

    const event = buildPromotionEvent({
      from: "candidate",
      to: "shadow",
      reason: "Passed held-out eval",
      evidence: { heldOutScore: 0.92 },
    });
    expect(event).toMatchObject({
      from: "candidate",
      to: "shadow",
      reason: "Passed held-out eval",
      evidence: { heldOutScore: 0.92 },
    });
    expect(event.timestamp).toBeTruthy();
  });

  it("lists scenario records and resolves the active model", () => {
    const records = [
      makeRecord({ artifactId: "model_1", scenario: "grid_ctf", activationState: "candidate" }),
      makeRecord({ artifactId: "model_2", scenario: "grid_ctf", activationState: "active" }),
      makeRecord({ artifactId: "model_3", scenario: "othello", activationState: "active" }),
    ];

    expect(
      listModelRecordsForScenario(records, "grid_ctf").map((record) => record.artifactId),
    ).toEqual(["model_1", "model_2"]);
    expect(resolveActiveModelRecord(records, "grid_ctf")?.artifactId).toBe("model_2");
    expect(resolveActiveModelRecord(records, "unknown")).toBeNull();
  });

  it("resolves an active model that fits the deployment VRAM target", () => {
    const records = [
      makeRecord({
        artifactId: "model_large",
        activationState: "active",
        trainingScale: {
          deviceCount: 4,
          shardingStrategy: "deepspeed_zero3",
          memoryLimitMb: 98_304,
          perDeviceMemoryLimitMb: 24_576,
          baseModelParameterCount: 32_000_000_000,
          baseModelQuantization: "nf4",
          deploymentTargetVramMb: 24_576,
        },
      }),
      makeRecord({
        artifactId: "model_small",
        activationState: "active",
        trainingScale: {
          deviceCount: 1,
          shardingStrategy: "none",
          memoryLimitMb: 16_384,
          perDeviceMemoryLimitMb: 16_384,
          baseModelParameterCount: 7_000_000_000,
          baseModelQuantization: "nf4",
          deploymentTargetVramMb: 16_384,
        },
      }),
    ];

    expect(
      resolveActiveModelRecord(records, "grid_ctf", { deploymentTargetVramMb: 16_384 })?.artifactId,
    ).toBe("model_small");
  });

  it("applies state transitions and displaces existing active models for the scenario", () => {
    const records = new Map<string, ModelRecord>([
      ["model_1", makeRecord({ artifactId: "model_1", activationState: "active" })],
      ["model_2", makeRecord({ artifactId: "model_2", activationState: "shadow" })],
    ]);

    applyModelStateTransition({
      records,
      artifactId: "model_2",
      targetState: "active",
      reason: "Shadow validated",
      evidence: { shadowRunScore: 0.9 },
    });

    expect(records.get("model_1")?.activationState).toBe("disabled");
    expect(records.get("model_1")?.promotionHistory[0]?.reason).toContain("Displaced by model_2");
    expect(records.get("model_2")?.activationState).toBe("active");
    expect(records.get("model_2")?.promotionHistory[0]).toMatchObject({
      from: "shadow",
      to: "active",
      reason: "Shadow validated",
      evidence: { shadowRunScore: 0.9 },
    });
  });
});
