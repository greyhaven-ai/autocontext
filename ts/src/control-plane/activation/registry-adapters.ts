import { promises as fs } from "node:fs";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { applyPatch } from "diff";

import type {
  ActuatorType,
  Artifact,
  Patch,
} from "../contract/types.js";
import {
  parseArtifactId,
  type ArtifactId,
  type EnvironmentTag,
  type Scenario,
} from "../contract/branded-ids.js";
import type { Registry } from "../registry/index.js";
import {
  deleteStatePointer,
  readStatePointer,
  writeStatePointer,
} from "../registry/state-pointer.js";
import { artifactDirectory } from "../registry/artifact-store.js";
import {
  getActuator,
  type WorkspaceLayoutArg,
} from "../actuators/registry.js";
import type { RuntimeActivationPointerStore } from "./types.js";
import type { RuntimeComponentGraphActivationDriverOptions } from "./component-graph-driver.js";

export interface RegistryRuntimeActivationPointerOptions {
  readonly registryRoot: string;
  readonly scenario: Scenario;
  readonly actuatorType: ActuatorType;
  readonly environmentTag: EnvironmentTag;
}

export function createRegistryRuntimeActivationPointerStore(
  options: RegistryRuntimeActivationPointerOptions,
): RuntimeActivationPointerStore {
  return {
    read: () => readStatePointer(
      options.registryRoot,
      options.scenario,
      options.actuatorType,
      options.environmentTag,
    ),
    write: (pointer) => {
      const artifactId = parseArtifactId(pointer.artifactId);
      if (artifactId === null) throw new Error("runtime activation pointer artifact id is invalid");
      writeStatePointer(
        options.registryRoot,
        options.scenario,
        options.actuatorType,
        options.environmentTag,
        { artifactId, asOf: pointer.asOf },
      );
    },
    clear: () => deleteStatePointer(
      options.registryRoot,
      options.scenario,
      options.actuatorType,
      options.environmentTag,
    ),
  };
}

export interface ActuatorRuntimeArtifactHooksOptions {
  readonly registry: Registry;
  readonly workingTreeRoot: string;
  readonly layout: WorkspaceLayoutArg;
  readonly incompatibleDependents?: (
    candidate: Artifact,
    baseline: Artifact,
  ) => readonly ArtifactId[];
}

export type ActuatorRuntimeArtifactHooks = Pick<
  RuntimeComponentGraphActivationDriverOptions,
  "applyArtifact" | "rollbackArtifact" | "restoreArtifact"
>;

/**
 * Connects transactional runtime activation to the existing actuator apply and
 * rollback paths. Rollback always invokes the actuator rollback contract before
 * re-applying the validated baseline payload.
 */
export function createActuatorRuntimeArtifactHooks(
  options: ActuatorRuntimeArtifactHooksOptions,
): ActuatorRuntimeArtifactHooks {
  const apply = async (artifact: Artifact): Promise<void> => {
    const registration = getActuator(artifact.actuatorType);
    if (!registration) throw new Error(`no actuator registered for ${artifact.actuatorType}`);
    await registration.actuator.apply({
      artifact,
      payloadDir: join(artifactDirectory(options.registry.cwd, artifact.id), "payload"),
      workingTreeRoot: options.workingTreeRoot,
      layout: options.layout,
    });
  };

  return {
    applyArtifact: async (artifactId) => {
      await apply(loadArtifact(options.registry, artifactId));
    },
    rollbackArtifact: async (candidateArtifactId, baselineArtifactId) => {
      const candidate = loadArtifact(options.registry, candidateArtifactId);
      if (baselineArtifactId === null) {
        throw new Error("actuator rollback requires an explicit baseline artifact");
      }
      const baseline = loadArtifact(options.registry, baselineArtifactId);
      if (
        candidate.actuatorType !== baseline.actuatorType
        || candidate.scenario !== baseline.scenario
        || candidate.environmentTag !== baseline.environmentTag
      ) {
        throw new Error("candidate and baseline artifact scopes do not match");
      }
      const registration = getActuator(candidate.actuatorType);
      if (!registration) throw new Error(`no actuator registered for ${candidate.actuatorType}`);
      const rollback = await registration.actuator.rollback({
        candidate,
        baseline,
        candidatePayloadDir: join(artifactDirectory(options.registry.cwd, candidate.id), "payload"),
        baselinePayloadDir: join(artifactDirectory(options.registry.cwd, baseline.id), "payload"),
        workingTreeRoot: options.workingTreeRoot,
        layout: options.layout,
        dependentsInIncompatibleState: options.incompatibleDependents?.(candidate, baseline),
      });
      const baselineTarget = resolveWorkingTreePath(
        options.workingTreeRoot,
        registration.actuator.resolveTargetPath(baseline, options.layout),
      );
      const restorePreimages = await applyRollbackPatches(
        Array.isArray(rollback) ? rollback : [rollback],
        options.workingTreeRoot,
        [baselineTarget],
      );
      try {
        await apply(baseline);
      } catch (applyError) {
        try {
          await restorePreimages();
        } catch (restoreError) {
          throw new AggregateError(
            [applyError, restoreError],
            "actuator rollback and preimage restoration failed",
          );
        }
        throw applyError;
      }
    },
    restoreArtifact: async (artifactId) => {
      if (artifactId === null) {
        throw new Error("actuator host restoration requires an explicit artifact");
      }
      await apply(loadArtifact(options.registry, artifactId));
    },
  };
}

function loadArtifact(registry: Registry, artifactId: string): Artifact {
  return registry.loadArtifact(parseRequiredArtifactId(artifactId));
}

function parseRequiredArtifactId(value: string): ArtifactId {
  const parsed = parseArtifactId(value);
  if (parsed === null) throw new Error("runtime activation artifact id is invalid");
  return parsed;
}

interface RuntimePatchPreimage {
  readonly path: string;
  readonly content: Uint8Array | null;
}

async function applyRollbackPatches(
  patches: readonly Patch[],
  workingTreeRoot: string,
  additionalPaths: readonly string[] = [],
): Promise<() => Promise<void>> {
  const patchPaths = patches.map((patch) =>
    resolveWorkingTreePath(workingTreeRoot, patch.filePath),
  );
  const paths = [...new Set([...patchPaths, ...additionalPaths])];
  const preimages = await Promise.all(paths.map(readPreimage));
  const restore = async (): Promise<void> => restorePreimages(preimages);

  try {
    for (let index = 0; index < patches.length; index += 1) {
      const patch = patches[index]!;
      const target = patchPaths[index]!;
      const current = await readTextIfPresent(target);
      const next = applyPatch(current ?? "", patch.unifiedDiff);
      if (next === false) {
        throw new Error(`actuator rollback patch did not apply cleanly: ${patch.filePath}`);
      }
      if (patch.afterContent !== undefined && next !== patch.afterContent) {
        throw new Error(`actuator rollback patch content mismatch: ${patch.filePath}`);
      }
      if (patch.operation === "delete") {
        await fs.rm(target, { force: true });
      } else {
        await fs.mkdir(dirname(target), { recursive: true });
        await fs.writeFile(target, next, "utf-8");
      }
    }
  } catch (applyError) {
    try {
      await restore();
    } catch (restoreError) {
      throw new AggregateError(
        [applyError, restoreError],
        "actuator rollback patch and preimage restoration failed",
      );
    }
    throw applyError;
  }

  return restore;
}

function resolveWorkingTreePath(workingTreeRoot: string, filePath: string): string {
  const root = resolve(workingTreeRoot);
  const target = isAbsolute(filePath) ? resolve(filePath) : resolve(root, filePath);
  const fromRoot = relative(root, target);
  if (fromRoot === ".." || fromRoot.startsWith(`..${process.platform === "win32" ? "\\" : "/"}`) || isAbsolute(fromRoot)) {
    throw new Error(`actuator rollback patch escapes working tree: ${filePath}`);
  }
  return target;
}

async function readPreimage(path: string): Promise<RuntimePatchPreimage> {
  try {
    return { path, content: new Uint8Array(await fs.readFile(path)) };
  } catch (error) {
    if (isMissingFileError(error)) return { path, content: null };
    throw error;
  }
}

async function readTextIfPresent(path: string): Promise<string | null> {
  try {
    return await fs.readFile(path, "utf-8");
  } catch (error) {
    if (isMissingFileError(error)) return null;
    throw error;
  }
}

async function restorePreimages(preimages: readonly RuntimePatchPreimage[]): Promise<void> {
  const errors: unknown[] = [];
  for (const preimage of [...preimages].reverse()) {
    try {
      if (preimage.content === null) {
        await fs.rm(preimage.path, { force: true });
      } else {
        await fs.mkdir(dirname(preimage.path), { recursive: true });
        await fs.writeFile(preimage.path, preimage.content);
      }
    } catch (error) {
      errors.push(error);
    }
  }
  if (errors.length > 0) {
    throw new AggregateError(errors, "actuator rollback preimage restoration failed");
  }
}

function isMissingFileError(error: unknown): boolean {
  return typeof error === "object"
    && error !== null
    && "code" in error
    && error.code === "ENOENT";
}
