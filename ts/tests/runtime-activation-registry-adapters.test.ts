import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import "../src/control-plane/actuators/index.js";
import { promptPatchActuator } from "../src/control-plane/actuators/prompt-patch/applicator.js";
import { createActuatorRuntimeArtifactHooks } from "../src/control-plane/activation/registry-adapters.js";
import { createArtifact } from "../src/control-plane/contract/factories.js";
import type { Artifact, Provenance } from "../src/control-plane/contract/types.js";
import { parseScenario, type Scenario } from "../src/control-plane/contract/branded-ids.js";
import { defaultWorkspaceLayout } from "../src/control-plane/emit/workspace-layout.js";
import { hashDirectory, openRegistry } from "../src/control-plane/registry/index.js";

const provenance: Provenance = {
  authorType: "human",
  authorId: "runtime-test",
  parentArtifactIds: [],
  createdAt: "2026-08-14T00:00:00.000Z",
};

describe("actuator runtime artifact hooks", () => {
  let root: string;

  beforeEach(() => {
    root = mkdtempSync(join(tmpdir(), "autocontext-runtime-hooks-"));
  });

  afterEach(() => {
    vi.restoreAllMocks();
    rmSync(root, { recursive: true, force: true });
  });

  test("applies the actuator rollback patch before restoring the baseline artifact", async () => {
    const registryRoot = join(root, "registry");
    const workingTreeRoot = join(root, "working-tree");
    mkdirSync(workingTreeRoot, { recursive: true });
    const registry = openRegistry(registryRoot);
    const layout = defaultWorkspaceLayout();
    const baseline = savePromptArtifact(registry, join(root, "baseline"), "baseline prompt\n");
    const candidate = savePromptArtifact(registry, join(root, "candidate"), "candidate prompt\n");
    const hooks = createActuatorRuntimeArtifactHooks({
      registry,
      workingTreeRoot,
      layout,
    });

    await hooks.applyArtifact!(candidate.id);
    const candidateTarget = join(
      workingTreeRoot,
      promptPatchActuator.resolveTargetPath(candidate, layout),
    );
    const baselineTarget = join(
      workingTreeRoot,
      promptPatchActuator.resolveTargetPath(baseline, layout),
    );
    expect(readFileSync(candidateTarget, "utf-8")).toBe("candidate prompt\n");
    expect(existsSync(baselineTarget)).toBe(false);

    await hooks.rollbackArtifact!(candidate.id, baseline.id);

    expect(readFileSync(candidateTarget, "utf-8")).toBe("baseline prompt\n");
    expect(readFileSync(baselineTarget, "utf-8")).toBe("baseline prompt\n");
  });

  test("restores every preimage when baseline application fails", async () => {
    const registryRoot = join(root, "registry");
    const workingTreeRoot = join(root, "working-tree");
    mkdirSync(workingTreeRoot, { recursive: true });
    const registry = openRegistry(registryRoot);
    const layout = defaultWorkspaceLayout();
    const baseline = savePromptArtifact(registry, join(root, "baseline"), "baseline prompt\n");
    const candidate = savePromptArtifact(registry, join(root, "candidate"), "candidate prompt\n");
    const hooks = createActuatorRuntimeArtifactHooks({ registry, workingTreeRoot, layout });
    await hooks.applyArtifact!(candidate.id);
    const candidateTarget = join(
      workingTreeRoot,
      promptPatchActuator.resolveTargetPath(candidate, layout),
    );
    const baselineTarget = join(
      workingTreeRoot,
      promptPatchActuator.resolveTargetPath(baseline, layout),
    );
    const apply = promptPatchActuator.apply.bind(promptPatchActuator);
    vi.spyOn(promptPatchActuator, "apply").mockImplementation(async (input) => {
      await apply(input);
      if (input.artifact.id === baseline.id) throw new Error("baseline apply failed");
    });

    await expect(hooks.rollbackArtifact!(candidate.id, baseline.id))
      .rejects.toThrow("baseline apply failed");

    expect(readFileSync(candidateTarget, "utf-8")).toBe("candidate prompt\n");
    expect(existsSync(baselineTarget)).toBe(false);
  });
});

function savePromptArtifact(
  registry: ReturnType<typeof openRegistry>,
  payloadDir: string,
  content: string,
): Artifact {
  mkdirSync(payloadDir, { recursive: true });
  writeFileSync(join(payloadDir, "prompt.txt"), content, "utf-8");
  const artifact = createArtifact({
    actuatorType: "prompt-patch",
    scenario: scenario("grid_ctf"),
    payloadHash: hashDirectory(payloadDir),
    provenance,
  });
  registry.saveArtifact(artifact, payloadDir);
  return artifact;
}

function scenario(value: string): Scenario {
  const parsed = parseScenario(value);
  if (parsed === null) throw new Error(`invalid test scenario: ${value}`);
  return parsed;
}
