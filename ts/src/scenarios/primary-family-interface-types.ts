import type { AgentTaskInterface as BaseAgentTaskInterface } from "../types/index.js";
import type { ScenarioInterface as BaseGameScenarioInterface } from "./game-interface.js";

export type GameScenarioInterface = BaseGameScenarioInterface;

export type AgentTaskInterface = BaseAgentTaskInterface;

export interface ArtifactEditingInterface {
  describeTask(): string;
  getRubric(): string;
  initialArtifacts(seed?: number): unknown[];
  getEditPrompt(artifacts: unknown[]): string;
  validateArtifact(artifact: unknown): unknown;
  evaluateEdits(original: unknown[], edited: unknown[]): unknown;
}
