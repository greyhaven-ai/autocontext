import type {
  ArtifactId,
  EnvironmentTag,
  Scenario,
} from "../contract/branded-ids.js";
import type { ActivationState, ActuatorType, Artifact } from "../contract/types.js";
import { listArtifactIds, loadArtifact } from "./artifact-store.js";
import { readStatePointer } from "./state-pointer.js";

export interface ListCandidatesFilter {
  readonly scenario?: Scenario;
  readonly environmentTag?: EnvironmentTag;
  readonly actuatorType?: ActuatorType;
  readonly activationState?: ActivationState;
}

export interface IndexCache {
  /**
   * List candidates matching the optional filter. v1: walks the filesystem
   * each call. A SQLite-backed implementation can land later behind the same
   * interface.
   */
  listCandidates(filter: ListCandidatesFilter): Artifact[];

  /**
   * Resolve the active Artifact for the (scenario, actuatorType, environmentTag)
   * tuple via the on-disk state pointer. Returns null if no pointer.
   */
  getByState(
    scenario: Scenario,
    actuatorType: ActuatorType,
    environmentTag: EnvironmentTag,
  ): Artifact | null;
}

/**
 * Filesystem-walking IndexCache implementation. Suitable for v1; designed
 * to be replaced by a SQLite-backed cache without changing call sites.
 */
export function createFsIndexCache(registryRoot: string): IndexCache {
  return {
    listCandidates(filter): Artifact[] {
      const ids = listArtifactIds(registryRoot);
      const out: Artifact[] = [];
      for (const id of ids) {
        let art: Artifact;
        try {
          art = loadArtifact(registryRoot, id as ArtifactId);
        } catch {
          // Skip unreadable artifacts; validate.ts is the place to surface them.
          continue;
        }
        if (filter.scenario !== undefined && art.scenario !== filter.scenario) continue;
        if (filter.environmentTag !== undefined && art.environmentTag !== filter.environmentTag) continue;
        if (filter.actuatorType !== undefined && art.actuatorType !== filter.actuatorType) continue;
        if (filter.activationState !== undefined && art.activationState !== filter.activationState) continue;
        out.push(art);
      }
      return out;
    },
    getByState(scenario, actuatorType, environmentTag): Artifact | null {
      const pointer = readStatePointer(registryRoot, scenario, actuatorType, environmentTag);
      if (pointer === null) return null;
      try {
        return loadArtifact(registryRoot, pointer.artifactId);
      } catch {
        return null;
      }
    },
  };
}
