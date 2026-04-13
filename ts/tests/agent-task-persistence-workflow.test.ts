import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  buildPersistedAgentTaskSpecData,
  persistAgentTaskScenario,
} from "../src/scenarios/agent-task-persistence-workflow.js";
import type { AgentTaskSpec } from "../src/scenarios/agent-task-spec.js";
import { getScenarioTypeMarker } from "../src/scenarios/families.js";

describe("agent task persistence workflow", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ac-agent-task-persist-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("builds persisted spec data and writes custom scenario files", () => {
    const spec: AgentTaskSpec = {
      taskPrompt: "Write about RLMs",
      judgeRubric: "Check accuracy",
      outputFormat: "free_text",
      judgeModel: "gpt-4o-mini",
      referenceContext: "RLM = Recursive Language Model",
      referenceSources: ["https://example.com/rlm"],
      requiredConcepts: ["context folding"],
      maxRounds: 3,
      qualityThreshold: 0.95,
      revisionPrompt: "Improve the draft",
      sampleInput: "topic=rlm",
    };

    expect(buildPersistedAgentTaskSpecData(spec)).toMatchObject({
      task_prompt: "Write about RLMs",
      judge_rubric: "Check accuracy",
      output_format: "free_text",
      judge_model: "gpt-4o-mini",
      reference_context: "RLM = Recursive Language Model",
      max_rounds: 3,
      quality_threshold: 0.95,
      revision_prompt: "Improve the draft",
      sample_input: "topic=rlm",
    });

    const scenarioDir = persistAgentTaskScenario({
      knowledgeRoot: dir,
      name: "recursive_language_models",
      spec,
    });

    expect(existsSync(join(scenarioDir, "agent_task_spec.json"))).toBe(true);
    expect(existsSync(join(scenarioDir, "scenario_type.txt"))).toBe(true);
    expect(readFileSync(join(scenarioDir, "scenario_type.txt"), "utf-8")).toBe(
      getScenarioTypeMarker("agent_task"),
    );
    expect(JSON.parse(readFileSync(join(scenarioDir, "agent_task_spec.json"), "utf-8"))).toMatchObject({
      task_prompt: "Write about RLMs",
      required_concepts: ["context folding"],
    });
  });
});
