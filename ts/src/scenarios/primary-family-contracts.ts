import type {
  AgentTaskInterface,
  ArtifactEditingInterface,
  GameScenarioInterface,
} from "./primary-family-interface-types.js";
import {
  isAgentTask,
  isArtifactEditing,
  isGameScenario,
} from "./primary-family-registry.js";

export type {
  AgentTaskInterface,
  ArtifactEditingInterface,
  GameScenarioInterface,
};

export { isAgentTask, isArtifactEditing, isGameScenario };
