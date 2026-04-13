import { hasMethodVariants } from "./family-contract-helpers.js";
import {
  isAgentTask as isRegisteredAgentTask,
  isGameScenario as isRegisteredGameScenario,
} from "./registry.js";
import type {
  AgentTaskInterface,
  ArtifactEditingInterface,
  GameScenarioInterface,
} from "./primary-family-interface-types.js";

export function isGameScenario(obj: unknown): obj is GameScenarioInterface {
  return isRegisteredGameScenario(obj);
}

export function isAgentTask(obj: unknown): obj is AgentTaskInterface {
  return isRegisteredAgentTask(obj);
}

export function isArtifactEditing(obj: unknown): obj is ArtifactEditingInterface {
  return hasMethodVariants(
    obj,
    ["describeTask", "describe_task"],
    ["getRubric", "get_rubric"],
    ["initialArtifacts", "initial_artifacts"],
    ["getEditPrompt", "get_edit_prompt"],
    ["validateArtifact", "validate_artifact"],
    ["evaluateEdits", "evaluate_edits"],
  );
}
