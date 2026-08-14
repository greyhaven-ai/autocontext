import type {
  ActuatorType,
  Artifact,
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
      await registration.actuator.rollback({
        candidate,
        baseline,
        candidatePayloadDir: join(artifactDirectory(options.registry.cwd, candidate.id), "payload"),
        baselinePayloadDir: join(artifactDirectory(options.registry.cwd, baseline.id), "payload"),
        workingTreeRoot: options.workingTreeRoot,
        layout: options.layout,
        dependentsInIncompatibleState: options.incompatibleDependents?.(candidate, baseline),
      });
      await apply(baseline);
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
import { join } from "node:path";
