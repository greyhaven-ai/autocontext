import { describe, expect, it } from "vitest";

import type { CreatedScenarioResult } from "../src/scenarios/scenario-creator.js";
import {
  buildScenarioDraft,
  buildScenarioPreviewInfo,
  reviseScenarioDraft,
} from "../src/scenarios/draft-workflow.js";

function humanizeName(name: string): string {
  return name
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

describe("scenario draft workflow", () => {
  it("builds a pending draft that preserves detected family while normalizing the interactive preview family", () => {
    const created: CreatedScenarioResult = {
      name: "incident_escalation",
      family: "operator_loop",
      spec: {
        taskPrompt: "Handle an outage escalation.",
        rubric: "Evaluate escalation quality.",
        description: "Escalation scenario",
      },
    };

    const draft = buildScenarioDraft({
      description: "Create a scenario for outage escalations.",
      created,
    });

    expect(draft.detectedFamily).toBe("operator_loop");
    expect(draft.preview.family).toBe("agent_task");
    expect(draft.validation.valid).toBe(true);
  });

  it("revises a draft while preserving prior core fields when the revision omits them", () => {
    const original = buildScenarioDraft({
      description: "Create a scenario for incident triage.",
      created: {
        name: "incident_triage",
        family: "agent_task",
        spec: {
          taskPrompt: "Summarize incident reports.",
          rubric: "Evaluate triage completeness.",
          description: "Incident report triage",
        },
      },
    });

    const revised = reviseScenarioDraft({
      draft: original,
      revisedSpec: {
        description: "Incident report triage with ownership assignment",
      },
    });

    expect(revised.preview.spec.taskPrompt).toBe("Summarize incident reports.");
    expect(revised.preview.spec.rubric).toBe("Evaluate triage completeness.");
    expect(revised.preview.spec.description).toBe(
      "Incident report triage with ownership assignment",
    );
  });

  it("builds preview info with mismatch guidance and normalized threshold", () => {
    const draft = buildScenarioDraft({
      description: "Create a scenario for outage escalations.",
      created: {
        name: "incident_escalation",
        family: "operator_loop",
        spec: {
          taskPrompt: "Handle an outage escalation.",
          rubric: "Evaluate escalation quality.",
          description: "Escalation scenario",
        },
      },
    });

    const preview = buildScenarioPreviewInfo(draft, { humanizeName });

    expect(preview.displayName).toBe("Incident Escalation");
    expect(preview.constraints.some((line: string) => line.includes("Detected operator_loop signals"))).toBe(true);
    expect(preview.winThreshold).toBeLessThanOrEqual(1);
  });
});
