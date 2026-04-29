/**
 * Prompt template assembly — buildPromptBundle (AC-345 Task 14).
 * Mirrors Python's autocontext/prompts/templates.py.
 */

import { compactPromptComponents } from "../knowledge/semantic-compaction.js";

export interface PromptContext {
  scenarioRules: string;
  strategyInterface: string;
  evaluationCriteria: string;
  playbook: string;
  trajectory: string;
  lessons: string;
  tools: string;
  hints: string;
  analysis: string;
}

export interface PromptBundle {
  competitor: string;
  analyst: string;
  coach: string;
  architect: string;
}

export function buildPromptBundle(ctx: PromptContext): PromptBundle {
  const compacted = compactPromptComponents({
    playbook: ctx.playbook,
    trajectory: ctx.trajectory,
    lessons: ctx.lessons,
    analysis: ctx.analysis,
  });
  const scenarioBlock = [
    "## Scenario Rules",
    ctx.scenarioRules,
    "",
    "## Strategy Interface",
    ctx.strategyInterface,
    "",
    "## Evaluation Criteria",
    ctx.evaluationCriteria,
  ].join("\n");

  const knowledgeBlock = [
    compacted.trajectory ? `\n${compacted.trajectory}\n` : "",
    compacted.playbook ? `## Current Playbook\n\n${compacted.playbook}\n` : "",
    compacted.lessons ? `## Operational Lessons\n\n${compacted.lessons}\n` : "",
    ctx.tools ? `## Available Tools\n\n${ctx.tools}\n` : "",
    ctx.hints ? `## Competitor Hints\n\n${ctx.hints}\n` : "",
    compacted.analysis ? `## Previous Analysis\n\n${compacted.analysis}\n` : "",
  ]
    .filter(Boolean)
    .join("\n");

  const competitor = [
    scenarioBlock,
    knowledgeBlock,
    "## Your Task",
    "Produce a JSON strategy that maximizes the evaluation criteria.",
  ].join("\n\n");

  const analyst = [
    scenarioBlock,
    knowledgeBlock,
    "## Your Task",
    "Analyze the current run. Structure your output with:",
    "## Findings",
    "## Root Causes",
    "## Actionable Recommendations",
  ].join("\n\n");

  const coach = [
    scenarioBlock,
    knowledgeBlock,
    "## Your Task",
    "Update the playbook based on the latest results. Use these markers:",
    "<!-- PLAYBOOK_START -->\n(updated playbook)\n<!-- PLAYBOOK_END -->",
    "<!-- LESSONS_START -->\n(operational lessons)\n<!-- LESSONS_END -->",
    "<!-- COMPETITOR_HINTS_START -->\n(competitor hints)\n<!-- COMPETITOR_HINTS_END -->",
  ].join("\n\n");

  const architect = [
    scenarioBlock,
    knowledgeBlock,
    "## Your Task",
    "Propose any tooling improvements. If tools are needed, include a JSON block:",
    '```json\n{"tools": [{"name": "...", "description": "...", "code": "..."}]}\n```',
  ].join("\n\n");

  return { competitor, analyst, coach, architect };
}
