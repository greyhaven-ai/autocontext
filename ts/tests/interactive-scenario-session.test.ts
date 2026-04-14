import { describe, expect, it, vi } from "vitest";

import { InteractiveScenarioSession } from "../src/server/interactive-scenario-session.js";

const humanizeName = (name: string): string => name.replace(/_/g, " ");
const provider = { name: "test", defaultModel: () => "test", complete: vi.fn() };

describe("interactive scenario session", () => {
  it("creates a pending draft preview from a natural-language description", async () => {
    const session = new InteractiveScenarioSession({
      knowledgeRoot: "/tmp/knowledge",
      humanizeName,
      deps: {
        createScenarioFromDescription: vi.fn(async () => ({
          name: "incident_triage",
          family: "agent_task",
          spec: {
            description: "Incident triage task",
            taskPrompt: "Summarize incident reports.",
            rubric: "Evaluate triage completeness.",
          },
        })),
      },
    });

    const preview = await session.createScenario({
      description: "Create an incident triage scenario.",
      provider,
    });

    expect(preview.name).toBe("incident_triage");
    expect(preview.description).toContain("Incident triage task");
  });

  it("revises the pending draft and preserves the updated preview", async () => {
    const session = new InteractiveScenarioSession({
      knowledgeRoot: "/tmp/knowledge",
      humanizeName,
      deps: {
        createScenarioFromDescription: vi.fn(async () => ({
          name: "incident_triage",
          family: "agent_task",
          spec: {
            description: "Incident triage task",
            taskPrompt: "Summarize incident reports.",
            rubric: "Evaluate triage completeness.",
          },
        })),
        reviseSpec: vi.fn(async () => ({
          changesApplied: true,
          revised: {
            description: "Incident triage task with owner assignment",
            taskPrompt: "Summarize incident reports and assign an owner.",
            rubric: "Evaluate triage completeness and owner assignment.",
          },
        })),
      },
    });

    await session.createScenario({
      description: "Create an incident triage scenario.",
      provider,
    });

    const revised = await session.reviseScenario({
      feedback: "Also require owner assignment.",
      provider,
    });

    expect(revised.description).toContain("owner assignment");
  });

  it("confirms the pending draft through persistence and clears the session", async () => {
    const session = new InteractiveScenarioSession({
      knowledgeRoot: "/tmp/knowledge",
      humanizeName,
      deps: {
        createScenarioFromDescription: vi.fn(async () => ({
          name: "incident_triage",
          family: "agent_task",
          spec: {
            description: "Incident triage task",
            taskPrompt: "Summarize incident reports.",
            rubric: "Evaluate triage completeness.",
          },
        })),
        persistInteractiveScenarioDraft: vi.fn(async () => ({
          persisted: true,
          generatedSource: false,
          scenarioDir: "/tmp/knowledge/_custom_scenarios/incident_triage",
          family: "agent_task",
          name: "incident_triage",
          errors: [],
        })),
      },
    });

    await session.createScenario({
      description: "Create an incident triage scenario.",
      provider,
    });

    const ready = await session.confirmScenario();
    expect(ready).toEqual({ name: "incident_triage", testScores: [] });

    await expect(session.reviseScenario({
      feedback: "Try another revision",
      provider,
    })).rejects.toThrow("No scenario preview is pending. Create a scenario first.");
  });
});
