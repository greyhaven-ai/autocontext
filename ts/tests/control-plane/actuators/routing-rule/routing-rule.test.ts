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
import { routingRuleActuator } from "../../../../src/control-plane/actuators/routing-rule/applicator.js";
import { routingRuleRegistration } from "../../../../src/control-plane/actuators/routing-rule/index.js";
import { CascadeRollbackRequired } from "../../../../src/control-plane/actuators/index.js";
import { hashDirectory } from "../../../../src/control-plane/registry/content-address.js";
import { createArtifact } from "../../../../src/control-plane/contract/factories.js";
import { defaultWorkspaceLayout } from "../../../../src/control-plane/emit/workspace-layout.js";
import type { ArtifactId } from "../../../../src/control-plane/contract/branded-ids.js";
import type { Artifact, Provenance } from "../../../../src/control-plane/contract/types.js";

const prov: Provenance = {
  authorType: "human",
  authorId: "jay@greyhaven.ai",
  parentArtifactIds: [],
  createdAt: "2026-04-17T00:00:00.000Z",
};

function mkPayload(dir: string, rule: object): string {
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "rule.json"), JSON.stringify(rule, null, 2), "utf-8");
  return dir;
}

function mkArtifact(payloadDir: string): Artifact {
  return createArtifact({
    actuatorType: "routing-rule",
    scenario: "grid_ctf",
    payloadHash: hashDirectory(payloadDir),
    provenance: prov,
  });
}

const VALID_RULE = {
  version: "1",
  rules: [
    { match: { pathPrefix: "/v1/users" }, route: "users-service" },
    { match: { methodIs: "GET" }, route: "read-service" },
  ],
};

describe("routing-rule actuator registration", () => {
  test("declares cascade-set rollback with tool-policy dependency and routing path pattern", () => {
    expect(routingRuleRegistration.type).toBe("routing-rule");
    expect(routingRuleRegistration.rollback.kind).toBe("cascade-set");
    if (routingRuleRegistration.rollback.kind === "cascade-set") {
      expect(routingRuleRegistration.rollback.dependsOn).toContain("tool-policy");
    }
    expect(routingRuleRegistration.allowedTargetPattern).toMatch(/routing/);
  });
});

describe("routing-rule actuator", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = mkdtempSync(join(tmpdir(), "autocontext-routing-rule-"));
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  test("parsePayload accepts a valid routing rule document and rejects malformed ones", () => {
    expect(routingRuleActuator.parsePayload(VALID_RULE)).toBeTruthy();
    expect(() => routingRuleActuator.parsePayload({ version: "2", rules: [] })).toThrow();
    expect(() => routingRuleActuator.parsePayload({ rules: [] })).toThrow();
    expect(() =>
      routingRuleActuator.parsePayload({ version: "1", rules: [{ match: {} }] }),
    ).toThrow();
  });

  test("resolveTargetPath places the rule file under <scenarioDir>/<routingSubdir>/ with .json", () => {
    const layout = defaultWorkspaceLayout();
    const payloadDir = mkPayload(join(tmp, "payload"), VALID_RULE);
    const artifact = mkArtifact(payloadDir);
    const target = routingRuleActuator.resolveTargetPath(artifact, layout);
    expect(target).toMatch(/routing\//);
    expect(target).toMatch(/\.json$/);
    expect(target).toContain(artifact.id);
  });

  test("apply writes the rule.json payload to the resolved target", async () => {
    const layout = defaultWorkspaceLayout();
    const payloadDir = mkPayload(join(tmp, "payload"), VALID_RULE);
    const artifact = mkArtifact(payloadDir);
    const wt = join(tmp, "wt");
    mkdirSync(wt, { recursive: true });

    await routingRuleActuator.apply({
      artifact,
      payloadDir,
      workingTreeRoot: wt,
      layout,
    });
    const target = join(wt, routingRuleActuator.resolveTargetPath(artifact, layout));
    expect(existsSync(target)).toBe(true);
    expect(JSON.parse(readFileSync(target, "utf-8"))).toEqual(VALID_RULE);
  });

  test("emitPatch roundtrips via diff.applyPatch", () => {
    const layout = defaultWorkspaceLayout();
    const payloadDir = mkPayload(join(tmp, "payload"), VALID_RULE);
    const artifact = mkArtifact(payloadDir);
    const wt = join(tmp, "wt");
    mkdirSync(wt, { recursive: true });

    const patch = routingRuleActuator.emitPatch({
      artifact,
      payloadDir,
      workingTreeRoot: wt,
      layout,
    });
    expect(patch.operation).toBe("create");
    expect(applyPatch("", patch.unifiedDiff)).toBe(patch.afterContent);
  });

  test("rollback throws CascadeRollbackRequired when dependents are in incompatible state", async () => {
    const layout = defaultWorkspaceLayout();
    const candDir = mkPayload(join(tmp, "cand"), VALID_RULE);
    const baseDir = mkPayload(join(tmp, "base"), { ...VALID_RULE, rules: [] });
    const candidate = mkArtifact(candDir);
    const baseline = createArtifact({
      actuatorType: "routing-rule",
      scenario: "grid_ctf",
      payloadHash: hashDirectory(baseDir),
      provenance: prov,
    });
    const wt = join(tmp, "wt");
    mkdirSync(wt, { recursive: true });
    const dependentId = "01KPEYB3BRQWK2WSHK9E93N6NP" as ArtifactId;

    await expect(
      routingRuleActuator.rollback({
        candidate,
        baseline,
        candidatePayloadDir: candDir,
        baselinePayloadDir: baseDir,
        workingTreeRoot: wt,
        layout,
        dependentsInIncompatibleState: [dependentId],
      }),
    ).rejects.toBeInstanceOf(CascadeRollbackRequired);
  });

  test("CascadeRollbackRequired carries the list of dependents", async () => {
    const layout = defaultWorkspaceLayout();
    const candDir = mkPayload(join(tmp, "cand"), VALID_RULE);
    const baseDir = mkPayload(join(tmp, "base"), { ...VALID_RULE, rules: [] });
    const candidate = mkArtifact(candDir);
    const baseline = createArtifact({
      actuatorType: "routing-rule",
      scenario: "grid_ctf",
      payloadHash: hashDirectory(baseDir),
      provenance: prov,
    });
    const wt = join(tmp, "wt");
    mkdirSync(wt, { recursive: true });
    const dependentIds = [
      "01KPEYB3BRQWK2WSHK9E93N6NP" as ArtifactId,
      "01KPEYB3BRQWK2WSHK9E93N6NQ" as ArtifactId,
    ];

    try {
      await routingRuleActuator.rollback({
        candidate,
        baseline,
        candidatePayloadDir: candDir,
        baselinePayloadDir: baseDir,
        workingTreeRoot: wt,
        layout,
        dependentsInIncompatibleState: dependentIds,
      });
      throw new Error("expected CascadeRollbackRequired");
    } catch (err) {
      expect(err).toBeInstanceOf(CascadeRollbackRequired);
      const cr = err as CascadeRollbackRequired;
      expect(cr.dependents).toEqual(dependentIds);
      expect(cr.name).toBe("CascadeRollbackRequired");
    }
  });

  test("rollback without dependents returns a content-reverting patch via cascade-set path", async () => {
    const layout = defaultWorkspaceLayout();
    const candDir = mkPayload(join(tmp, "cand"), VALID_RULE);
    const baseDir = mkPayload(join(tmp, "base"), { ...VALID_RULE, rules: [] });
    const candidate = mkArtifact(candDir);
    const baseline = createArtifact({
      actuatorType: "routing-rule",
      scenario: "grid_ctf",
      payloadHash: hashDirectory(baseDir),
      provenance: prov,
    });
    const wt = join(tmp, "wt");
    mkdirSync(wt, { recursive: true });
    await routingRuleActuator.apply({
      artifact: candidate,
      payloadDir: candDir,
      workingTreeRoot: wt,
      layout,
    });

    const patches = await routingRuleActuator.rollback({
      candidate,
      baseline,
      candidatePayloadDir: candDir,
      baselinePayloadDir: baseDir,
      workingTreeRoot: wt,
      layout,
    });
    const patch = Array.isArray(patches) ? patches[0]! : patches;
    const target = join(wt, routingRuleActuator.resolveTargetPath(candidate, layout));
    expect(patch.afterContent).toBe(readFileSync(join(baseDir, "rule.json"), "utf-8"));
    expect(applyPatch(readFileSync(target, "utf-8"), patch.unifiedDiff)).toBe(patch.afterContent);
  });
});

describe("CascadeRollbackRequired", () => {
  test("is an Error subclass with typed dependents property", () => {
    const ids = ["01KPEYB3BRQWK2WSHK9E93N6NP" as ArtifactId];
    const err = new CascadeRollbackRequired(
      "rollback blocked by active dependents",
      ids,
    );
    expect(err).toBeInstanceOf(Error);
    expect(err.message).toBe("rollback blocked by active dependents");
    expect(err.dependents).toEqual(ids);
    expect(err.name).toBe("CascadeRollbackRequired");
  });

  test("dependents is a readonly snapshot (array copy — caller mutations don't leak in)", () => {
    const src: ArtifactId[] = ["01KPEYB3BRQWK2WSHK9E93N6NP" as ArtifactId];
    const err = new CascadeRollbackRequired("x", src);
    src.push("01KPEYB3BRQWK2WSHK9E93N6NQ" as ArtifactId);
    expect(err.dependents.length).toBe(1);
  });
});
