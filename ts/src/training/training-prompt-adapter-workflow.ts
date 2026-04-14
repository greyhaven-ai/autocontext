import {
  formatPromptTrajectory,
  readPromptContextString,
} from "./prompt-alignment-helpers.js";
import type {
  PromptPair,
  ShareGPTExample,
  TrainingPromptRecord,
} from "./prompt-alignment-types.js";

export function adaptTrainingPromptRecord(record: TrainingPromptRecord): PromptPair {
  const context = record.context;
  const systemParts: string[] = [];
  const scenarioRules = readPromptContextString(context, "scenarioRules", "scenario_rules");
  const strategyInterface = readPromptContextString(context, "strategyInterface", "strategy_interface");
  const evaluationCriteria = readPromptContextString(context, "evaluationCriteria", "evaluation_criteria");
  const trajectory = formatPromptTrajectory(context.trajectory);
  const playbook = readPromptContextString(context, "playbook");
  const lessons = readPromptContextString(context, "lessons", "operationalLessons", "operational_lessons");
  const tools = readPromptContextString(context, "tools", "availableTools", "available_tools");
  const hints = readPromptContextString(context, "hints", "competitorHints", "competitor_hints");
  const analysis = readPromptContextString(context, "analysis", "previousAnalysis", "previous_analysis");

  if (scenarioRules) {
    systemParts.push(`## Scenario Rules\n${scenarioRules}`);
  }
  if (strategyInterface) {
    systemParts.push(`## Strategy Interface\n${strategyInterface}`);
  }
  if (evaluationCriteria) {
    systemParts.push(`## Evaluation Criteria\n${evaluationCriteria}`);
  }
  if (trajectory) {
    systemParts.push(trajectory);
  }
  if (playbook) {
    systemParts.push(`## Current Playbook\n\n${playbook}`);
  }
  if (lessons) {
    systemParts.push(`## Operational Lessons\n\n${lessons}`);
  }
  if (tools) {
    systemParts.push(`## Available Tools\n\n${tools}`);
  }
  if (hints) {
    systemParts.push(`## Competitor Hints\n\n${hints}`);
  }
  if (analysis) {
    systemParts.push(`## Previous Analysis\n\n${analysis}`);
  }

  return {
    system: systemParts.join("\n\n"),
    user: "Produce a JSON strategy that maximizes the evaluation criteria.",
    expectedOutput: record.strategy,
  };
}

export function buildTrainingShareGptExample(
  record: TrainingPromptRecord,
): ShareGPTExample {
  const pair = adaptTrainingPromptRecord(record);
  return {
    conversations: [
      { from: "system", value: pair.system },
      { from: "human", value: pair.user },
      { from: "gpt", value: record.strategy },
    ],
    metadata: {
      scenario: record.scenario,
      score: record.score,
      contractVersion: "1.0",
    },
  };
}
