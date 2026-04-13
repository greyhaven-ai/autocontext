import { describe, expect, it, beforeEach, afterEach } from "vitest";
import { existsSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { mkdtempSync } from "node:fs";

import { buildScenarioDraft } from "../src/scenarios/draft-workflow.js";
import { persistInteractiveScenarioDraft } from "../src/scenarios/interactive-scenario-materialization.js";

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-interactive-materialize-"));
});

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

describe("interactive scenario materialization", () => {
  it("persists the interactive draft as an agent_task scaffold with validation metadata", async () => {
    const draft = buildScenarioDraft({
      description: "Create a scenario about incident report triage.",
      created: {
        name: "incident_triage",
        family: "operator_loop",
        spec: {
          taskPrompt: "Summarize incident reports with a triage focus.",
          rubric: "Evaluate triage completeness and clarity.",
          description: "Summarize incident reports with a triage focus.",
        },
      },
    });

    const result = await persistInteractiveScenarioDraft({
      draft,
      knowledgeRoot: tmpDir,
    });

    expect(result.persisted).toBe(true);
    const scenarioDir = join(tmpDir, "_custom_scenarios", "incident_triage");
    expect(existsSync(join(scenarioDir, "scenario_type.txt"))).toBe(true);
    expect(readFileSync(join(scenarioDir, "scenario_type.txt"), "utf-8").trim()).toBe("agent_task");

    const spec = JSON.parse(readFileSync(join(scenarioDir, "spec.json"), "utf-8")) as Record<string, unknown>;
    expect(spec.taskPrompt).toBe("Summarize incident reports with a triage focus.");
    expect(spec.intent_confidence).toBeTypeOf("number");
    expect(Array.isArray(spec.intent_issues)).toBe(true);

    const agentTaskSpec = JSON.parse(
      readFileSync(join(scenarioDir, "agent_task_spec.json"), "utf-8"),
    ) as Record<string, unknown>;
    expect(agentTaskSpec.task_prompt).toBe("Summarize incident reports with a triage focus.");
    expect(agentTaskSpec.judge_rubric).toBe("Evaluate triage completeness and clarity.");
  });
});
