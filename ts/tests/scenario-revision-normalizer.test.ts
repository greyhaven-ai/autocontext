import { describe, expect, it } from "vitest";

import { normalizeScenarioRevisionSpec } from "../src/scenarios/revision-spec-normalizer.js";

describe("scenario revision spec normalizer", () => {
  it("normalizes agent task revision payloads into the parsed spec shape", () => {
    const normalized = normalizeScenarioRevisionSpec("agent_task", {
      task_prompt: "Summarize outages with ownership.",
      rubric: "Evaluate correctness.",
      description: "Outage triage task",
      max_rounds: 2,
      quality_threshold: 0.8,
    });

    expect(normalized).toMatchObject({
      taskPrompt: "Summarize outages with ownership.",
      judgeRubric: "Evaluate correctness.",
      rubric: "Evaluate correctness.",
      description: "Outage triage task",
      maxRounds: 2,
      qualityThreshold: 0.8,
    });
  });

  it("normalizes simulation revision payloads with camelCase and snake_case aliases", () => {
    const normalized = normalizeScenarioRevisionSpec("simulation", {
      description: "Escalation simulation",
      environmentDescription: "Support queue",
      initial_state_description: "Ticket backlog",
      successCriteria: ["resolve the outage", "avoid regressions"],
      failure_modes: ["timeout"],
      maxSteps: 12,
      actions: [
        {
          name: "ask",
          description: "Ask for clarification",
          parameters: {},
          preconditions: [],
          effects: ["context"],
        },
        {
          name: "escalate",
          description: "Escalate to an operator",
          parameters: {},
          preconditions: ["ask"],
          effects: ["operator_review"],
        },
      ],
    });

    expect(normalized).toMatchObject({
      description: "Escalation simulation",
      environmentDescription: "Support queue",
      initialStateDescription: "Ticket backlog",
      successCriteria: ["resolve the outage", "avoid regressions"],
      failureModes: ["timeout"],
      maxSteps: 12,
    });
  });

  it("throws for unsupported revision families", () => {
    expect(() =>
      normalizeScenarioRevisionSpec("unknown_family", {
        description: "Unsupported",
      }),
    ).toThrow(/Unsupported scenario family/);
  });
});
