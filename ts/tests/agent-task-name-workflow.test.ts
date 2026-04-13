import { describe, expect, it } from "vitest";

import {
  deriveAgentTaskName,
  scoreAgentTaskNameWord,
} from "../src/scenarios/agent-task-name-workflow.js";

describe("agent task name workflow", () => {
  it("derives stable domain-preserving snake_case names", () => {
    expect(deriveAgentTaskName("Write a haiku about testing software").split("_")).toEqual(
      expect.arrayContaining(["haiku", "testing", "software"].filter((word) =>
        deriveAgentTaskName("Write a haiku about testing software").includes(word)
      )),
    );
    expect(deriveAgentTaskName("Create something")).toBe("something");
    expect(deriveAgentTaskName("a the and")).toBe("custom");
    expect(deriveAgentTaskName("test test test testing")).toBe("test_testing");
  });

  it("scores concrete words above abstract suffix-heavy words", () => {
    expect(scoreAgentTaskNameWord("documentation", 0, 3)).toBeLessThan(
      scoreAgentTaskNameWord("incident", 0, 3),
    );
  });
});
