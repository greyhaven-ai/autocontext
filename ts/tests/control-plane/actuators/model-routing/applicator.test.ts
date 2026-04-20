import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { applyPatch } from "diff";
import {
  mkdtempSync,
  mkdirSync,
  rmSync,
  writeFileSync,
  readFileSync,
  existsSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { modelRoutingActuator } from "../../../../src/control-plane/actuators/model-routing/applicator.js";
import { hashDirectory } from "../../../../src/control-plane/registry/content-address.js";
import { createArtifact } from "../../../../src/control-plane/contract/factories.js";
import { defaultWorkspaceLayout } from "../../../../src/control-plane/emit/workspace-layout.js";
import { parseContentHash } from "../../../../src/control-plane/contract/branded-ids.js";
import type { Artifact, Provenance } from "../../../../src/control-plane/contract/types.js";
import type { ModelRoutingPayload } from "../../../../src/control-plane/actuators/model-routing/schema.js";

const prov: Provenance = {
  authorType: "human",
  authorId: "jay@greyhaven.ai",
  parentArtifactIds: [],
  createdAt: "2026-04-17T00:00:00.000Z",
};

const VALID_PAYLOAD: ModelRoutingPayload = {
  schemaVersion: "1.0",
  default: { provider: "anthropic", model: "claude-sonnet-4-5", endpoint: null },
  routes: [
    {
      id: "checkout-specialized",
      match: { "env.taskType": { equals: "checkout" } },
      target: {
        provider: "openai-compatible",
        model: "finetuned-checkout-v3",
        endpoint: "https://my-vllm/v1",
      },
      rollout: { percent: 25, cohortKey: "session.sessionIdHash" },
      budget: { maxCostUsdPerCall: 0.02 },
      latency: { maxP95Ms: 800 },
      confidence: { minScore: 0.85 },
    },
  ],
  fallback: [
    {
      provider: "anthropic",
      model: "claude-haiku-4-5",
      when: ["budget-exceeded", "latency-breached", "provider-error"],
    },
  ],
};

function mkPayload(dir: string, payload: unknown): string {
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "models.json"), JSON.stringify(payload, null, 2), "utf-8");
  return dir;
}

function mkArtifact(payloadDir: string): Artifact {
  return createArtifact({
    actuatorType: "model-routing",
    scenario: "grid_ctf",
    payloadHash: hashDirectory(payloadDir),
    provenance: prov,
  });
}

describe("model-routing actuator — parsePayload", () => {
  test("accepts a fully-populated spec §4 example", () => {
    expect(modelRoutingActuator.parsePayload(VALID_PAYLOAD)).toBeTruthy();
  });

  test("accepts a minimal payload (default + empty routes + empty fallback)", () => {
    expect(
      modelRoutingActuator.parsePayload({
        schemaVersion: "1.0",
        default: { provider: "anthropic", model: "claude-sonnet-4-5" },
        routes: [],
        fallback: [],
      }),
    ).toBeTruthy();
  });

  test("rejects wrong schemaVersion", () => {
    expect(() =>
      modelRoutingActuator.parsePayload({ ...VALID_PAYLOAD, schemaVersion: "2.0" }),
    ).toThrow();
  });

  test("rejects a route missing required id", () => {
    const bad = {
      ...VALID_PAYLOAD,
      routes: [{ match: {}, target: { provider: "x", model: "y" } }],
    };
    expect(() => modelRoutingActuator.parsePayload(bad)).toThrow();
  });

  test("rejects empty route match expressions", () => {
    const bad = {
      ...VALID_PAYLOAD,
      routes: [{ ...VALID_PAYLOAD.routes[0]!, match: {} }],
    };
    expect(() => modelRoutingActuator.parsePayload(bad)).toThrow(/match expression/i);
  });

  test("rejects match operators with more than one operator", () => {
    const bad = {
      ...VALID_PAYLOAD,
      routes: [
        {
          ...VALID_PAYLOAD.routes[0]!,
          match: { "env.taskType": { default: true, equals: "checkout" } },
        },
      ],
    };
    expect(() => modelRoutingActuator.parsePayload(bad)).toThrow(/exactly one/i);
  });

  test("rejects a rollout with percent > 100", () => {
    const bad = {
      ...VALID_PAYLOAD,
      routes: [
        {
          ...VALID_PAYLOAD.routes[0]!,
          rollout: { percent: 150, cohortKey: "x" },
        },
      ],
    };
    expect(() => modelRoutingActuator.parsePayload(bad)).toThrow();
  });

  test("rejects additionalProperties (strict)", () => {
    expect(() =>
      modelRoutingActuator.parsePayload({ ...VALID_PAYLOAD, extraField: "nope" }),
    ).toThrow();
  });
});

describe("model-routing actuator — apply / emit / rollback", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = mkdtempSync(join(tmpdir(), "autocontext-model-routing-"));
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  test("resolveTargetPath places models.json under <scenarioDir>/<routingSubdir>/models/", () => {
    const layout = defaultWorkspaceLayout();
    const payloadDir = mkPayload(join(tmp, "payload"), VALID_PAYLOAD);
    const artifact = mkArtifact(payloadDir);
    const target = modelRoutingActuator.resolveTargetPath(artifact, layout);
    expect(target).toMatch(/routing\/models\//);
    expect(target).toMatch(/\.json$/);
    expect(target).toContain(artifact.id);
  });

  test("apply writes the models.json payload to the resolved target", async () => {
    const layout = defaultWorkspaceLayout();
    const payloadDir = mkPayload(join(tmp, "payload"), VALID_PAYLOAD);
    const artifact = mkArtifact(payloadDir);
    const wt = join(tmp, "wt");
    mkdirSync(wt, { recursive: true });

    await modelRoutingActuator.apply({
      artifact,
      payloadDir,
      workingTreeRoot: wt,
      layout,
    });
    const target = join(wt, modelRoutingActuator.resolveTargetPath(artifact, layout));
    expect(existsSync(target)).toBe(true);
    expect(JSON.parse(readFileSync(target, "utf-8"))).toEqual(VALID_PAYLOAD);
  });

  test("apply rejects when payload tree hash does not match artifact.payloadHash", async () => {
    const layout = defaultWorkspaceLayout();
    const payloadDir = mkPayload(join(tmp, "payload"), VALID_PAYLOAD);
    const artifact = createArtifact({
      actuatorType: "model-routing",
      scenario: "grid_ctf",
      payloadHash: parseContentHash("sha256:" + "0".repeat(64))!,
      provenance: prov,
    });
    const wt = join(tmp, "wt");
    mkdirSync(wt, { recursive: true });
    await expect(
      modelRoutingActuator.apply({
        artifact,
        payloadDir,
        workingTreeRoot: wt,
        layout,
      }),
    ).rejects.toThrow(/hash.*mismatch/i);
  });

  test("emitPatch roundtrips via diff.applyPatch", () => {
    const layout = defaultWorkspaceLayout();
    const payloadDir = mkPayload(join(tmp, "payload"), VALID_PAYLOAD);
    const artifact = mkArtifact(payloadDir);
    const wt = join(tmp, "wt");
    mkdirSync(wt, { recursive: true });

    const patch = modelRoutingActuator.emitPatch({
      artifact,
      payloadDir,
      workingTreeRoot: wt,
      layout,
    });
    expect(patch.operation).toBe("create");
    expect(applyPatch("", patch.unifiedDiff)).toBe(patch.afterContent);
  });

  test("rollback content-reverts to the baseline payload", async () => {
    const layout = defaultWorkspaceLayout();
    const candDir = mkPayload(join(tmp, "cand"), {
      ...VALID_PAYLOAD,
      default: { provider: "anthropic", model: "claude-opus-4-5" },
    });
    const baseDir = mkPayload(join(tmp, "base"), VALID_PAYLOAD);
    const candidate = mkArtifact(candDir);
    const baseline = createArtifact({
      actuatorType: "model-routing",
      scenario: "grid_ctf",
      payloadHash: hashDirectory(baseDir),
      provenance: prov,
    });
    const wt = join(tmp, "wt");
    mkdirSync(wt, { recursive: true });
    await modelRoutingActuator.apply({
      artifact: candidate,
      payloadDir: candDir,
      workingTreeRoot: wt,
      layout,
    });

    const patches = await modelRoutingActuator.rollback({
      candidate,
      baseline,
      candidatePayloadDir: candDir,
      baselinePayloadDir: baseDir,
      workingTreeRoot: wt,
      layout,
    });
    const patch = Array.isArray(patches) ? patches[0]! : patches;
    const target = join(wt, modelRoutingActuator.resolveTargetPath(candidate, layout));
    expect(patch.afterContent).toBe(readFileSync(join(baseDir, "models.json"), "utf-8"));
    expect(applyPatch(readFileSync(target, "utf-8"), patch.unifiedDiff)).toBe(patch.afterContent);
  });
});
