import { describe, expect, it } from "vitest";
import { AGENT_TASK_DESIGNER_SYSTEM } from "../src/scenarios/agent-task-designer.js";

describe("designer calibration examples", () => {
  it("system prompt requires calibration examples", () => {
    expect(AGENT_TASK_DESIGNER_SYSTEM).toContain(
      "MUST include at least 2 calibration",
    );
  });

  it("example spec in prompt includes calibration_examples with required fields", () => {
    expect(AGENT_TASK_DESIGNER_SYSTEM).toContain('"calibration_examples"');
    expect(AGENT_TASK_DESIGNER_SYSTEM).toContain('"human_score"');
    expect(AGENT_TASK_DESIGNER_SYSTEM).toContain('"human_notes"');
  });
});
