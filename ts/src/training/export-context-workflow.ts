import { extractDelimitedSection } from "../agents/roles.js";
import type { ArtifactStore } from "../knowledge/artifact-store.js";
import { resolveCustomAgentTask } from "../scenarios/custom-loader.js";
import { AGENT_TASK_REGISTRY, SCENARIO_REGISTRY } from "../scenarios/registry.js";

export function extractTrainingHints(playbook: string): string {
  return extractDelimitedSection(
    playbook,
    "<!-- COMPETITOR_HINTS_START -->",
    "<!-- COMPETITOR_HINTS_END -->",
  ) ?? "";
}

export function buildTrajectorySnippet(
  generations: Array<{
    generation_index: number;
    best_score: number;
    gate_decision: string;
  }>,
  upToIndex: number,
): Array<Record<string, unknown>> {
  return generations
    .filter((generation) => generation.generation_index <= upToIndex)
    .map((generation) => ({
      generation_index: generation.generation_index,
      best_score: generation.best_score,
      gate_decision: generation.gate_decision,
    }));
}

export function resolveTrainingPromptContext(
  artifacts: ArtifactStore,
  scenarioName: string,
): Record<string, unknown> {
  const gameFactory = SCENARIO_REGISTRY[scenarioName];
  if (gameFactory) {
    const scenario = new gameFactory();
    return {
      scenarioRules: scenario.describeRules(),
      strategyInterface: scenario.describeStrategyInterface(),
      evaluationCriteria: scenario.describeEvaluationCriteria(),
    };
  }

  const builtinTaskFactory = AGENT_TASK_REGISTRY[scenarioName];
  if (builtinTaskFactory) {
    const task = new builtinTaskFactory();
    return {
      scenarioRules: task.describeTask(),
      strategyInterface: "Respond with output matching the task requirements.",
      evaluationCriteria: task.getRubric(),
    };
  }

  const customTask = resolveCustomAgentTask(artifacts.knowledgeRoot, scenarioName);
  if (customTask) {
    const outputFormat = customTask.spec.outputFormat === "json_schema"
      ? "Respond with JSON output matching the task requirements."
      : `Respond with ${customTask.spec.outputFormat} output matching the task requirements.`;
    return {
      scenarioRules: customTask.spec.taskPrompt,
      strategyInterface: outputFormat,
      evaluationCriteria: customTask.spec.judgeRubric,
    };
  }

  return {};
}
