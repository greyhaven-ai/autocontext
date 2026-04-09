/**
 * Agent-task family codegen — generates JS source from an AgentTaskSpec (AC-436).
 * Mirrors Python's autocontext/scenarios/custom/agent_task_codegen.py.
 */

import { renderCodegenTemplate } from "./template-renderer.js";
import { AGENT_TASK_SCENARIO_TEMPLATE } from "./templates/agent-task-template.js";

export function generateAgentTaskSource(
  spec: Record<string, unknown>,
  name: string,
): string {
  const taskPrompt = String(spec.taskPrompt ?? spec.task_prompt ?? "");
  const judgeRubric = String(
    spec.judgeRubric ?? spec.judge_rubric ?? spec.rubric ?? "",
  );
  const description = String(spec.description ?? `Agent task: ${name}`);
  const outputFormat = String(spec.outputFormat ?? spec.output_format ?? "free_text");
  const maxRounds = Number(spec.maxRounds ?? spec.max_rounds ?? 1);
  const qualityThreshold = Number(spec.qualityThreshold ?? spec.quality_threshold ?? 0.9);

  return renderCodegenTemplate(AGENT_TASK_SCENARIO_TEMPLATE, {
    __SCENARIO_NAME_COMMENT__: name,
    __SCENARIO_NAME__: JSON.stringify(name),
    __TASK_PROMPT__: JSON.stringify(taskPrompt),
    __JUDGE_RUBRIC__: JSON.stringify(judgeRubric),
    __DESCRIPTION__: JSON.stringify(description),
    __OUTPUT_FORMAT__: JSON.stringify(outputFormat),
    __MAX_ROUNDS__: String(maxRounds),
    __QUALITY_THRESHOLD__: String(qualityThreshold),
  });
}
