import { afterEach, describe, expect, it } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import { resolveRunnableScenarioClass } from "../src/cli/runnable-scenario-resolution.js";
import { loadCustomScenarios } from "../src/scenarios/custom-loader.js";
import { createPersistedParametricScenarioClass } from "../src/scenarios/persisted-parametric-scenario.js";

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac551-parametric-"));
}

function writeSavedParametricScenario(
  knowledgeRoot: string,
  name = "linear_outage_escalation",
): void {
  const scenarioDir = join(knowledgeRoot, "_custom_scenarios", name);
  mkdirSync(scenarioDir, { recursive: true });
  writeFileSync(
    join(scenarioDir, "spec.json"),
    JSON.stringify(
      {
        name,
        display_name: "Linear Outage Escalation",
        description: "Escalate likely Linear outages while avoiding unnecessary paging.",
        strategy_interface_description:
          "Return JSON with clarification_threshold and escalation_bias floats in [0,1].",
        evaluation_criteria: "Reward correct outage escalation timing.",
        strategy_params: [
          {
            name: "clarification_threshold",
            description: "How much clarification to gather before escalating.",
            min_value: 0,
            max_value: 1,
            default: 0.4,
          },
          {
            name: "escalation_bias",
            description: "How quickly to escalate a likely outage.",
            min_value: 0,
            max_value: 1,
            default: 0.6,
          },
        ],
        constraints: [
          {
            expression: "clarification_threshold + escalation_bias",
            operator: "<=",
            threshold: 1.5,
            description: "Do not over-index on both clarification and escalation.",
          },
        ],
        environment_variables: [
          {
            name: "incident_severity",
            description: "Severity of the outage.",
            low: 0.2,
            high: 0.95,
          },
        ],
        scoring_components: [
          {
            name: "outage_capture",
            description: "Ability to escalate real outages quickly.",
            formula_terms: {
              clarification_threshold: -0.1,
              escalation_bias: 0.7,
            },
            noise_range: [0, 0],
          },
        ],
        final_score_weights: {
          outage_capture: 1,
        },
        win_threshold: 0.5,
        observation_constraints: ["Ask targeted questions when ambiguity is high."],
        scenario_type: "parametric",
      },
      null,
      2,
    ),
    "utf-8",
  );
}

describe("persisted parametric scenario", () => {
  const tempDirs: string[] = [];

  afterEach(() => {
    for (const dir of tempDirs.splice(0)) {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("infers the parametric type from spec metadata when scenario_type.txt is missing", () => {
    const dir = makeTempDir();
    tempDirs.push(dir);
    const knowledgeRoot = join(dir, "knowledge");
    writeSavedParametricScenario(knowledgeRoot);

    const loaded = loadCustomScenarios(join(knowledgeRoot, "_custom_scenarios"));
    const entry = loaded.get("linear_outage_escalation");

    expect(entry?.type).toBe("parametric");
  });

  it("creates a runnable scenario class from a saved parametric spec", () => {
    const ScenarioClass = createPersistedParametricScenarioClass("linear_outage_escalation", {
      name: "linear_outage_escalation",
      display_name: "Linear Outage Escalation",
      description: "Escalate likely Linear outages while avoiding unnecessary paging.",
      strategy_interface_description:
        "Return JSON with clarification_threshold and escalation_bias floats in [0,1].",
      evaluation_criteria: "Reward correct outage escalation timing.",
      strategy_params: [
        {
          name: "clarification_threshold",
          description: "How much clarification to gather before escalating.",
          min_value: 0,
          max_value: 1,
          default: 0.4,
        },
        {
          name: "escalation_bias",
          description: "How quickly to escalate a likely outage.",
          min_value: 0,
          max_value: 1,
          default: 0.6,
        },
      ],
      constraints: [
        {
          expression: "clarification_threshold + escalation_bias",
          operator: "<=",
          threshold: 1.5,
          description: "Do not over-index on both clarification and escalation.",
        },
      ],
      environment_variables: [
        {
          name: "incident_severity",
          description: "Severity of the outage.",
          low: 0.2,
          high: 0.95,
        },
      ],
      scoring_components: [
        {
          name: "outage_capture",
          description: "Ability to escalate real outages quickly.",
          formula_terms: {
            clarification_threshold: -0.1,
            escalation_bias: 0.7,
          },
          noise_range: [0, 0],
        },
      ],
      final_score_weights: {
        outage_capture: 1,
      },
      win_threshold: 0.5,
      observation_constraints: ["Ask targeted questions when ambiguity is high."],
    });

    const scenario = new ScenarioClass();
    const result = scenario.executeMatch(
      {
        clarification_threshold: 0.35,
        escalation_bias: 0.65,
      },
      1,
    );

    expect(scenario.name).toBe("linear_outage_escalation");
    expect(result.validationErrors).toEqual([]);
    expect(result.score).toBeGreaterThan(0);
    expect(result.summary).toContain("Linear Outage Escalation");
  });

  it("resolves saved parametric scenarios by name for run and benchmark", () => {
    const dir = makeTempDir();
    tempDirs.push(dir);
    const knowledgeRoot = join(dir, "knowledge");
    writeSavedParametricScenario(knowledgeRoot);

    const ScenarioClass = resolveRunnableScenarioClass({
      scenarioName: "linear_outage_escalation",
      builtinScenarios: {},
      knowledgeRoot,
    });

    const scenario = new ScenarioClass();
    const result = scenario.executeMatch(
      {
        clarification_threshold: 0.4,
        escalation_bias: 0.6,
      },
      0,
    );

    expect(scenario.name).toBe("linear_outage_escalation");
    expect(result.validationErrors).toEqual([]);
    expect(result.score).toBeGreaterThan(0);
  });
});
