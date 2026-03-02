import { describe, it, expect } from "vitest";
import { SkillPackage, exportAgentTaskSkill, cleanLessons } from "../src/knowledge/index.js";
import type { SkillPackageData } from "../src/knowledge/index.js";

function makeExampleOutputs() {
  return [
    { output: "Great answer", score: 0.95, reasoning: "Thorough and accurate" },
    { output: "Okay answer", score: 0.70, reasoning: "Partially correct" },
    { output: "Weak answer", score: 0.30, reasoning: "Missing key points" },
  ];
}

function makeAgentTaskPackage(overrides?: Partial<SkillPackageData>): SkillPackage {
  return new SkillPackage({
    scenarioName: "test_task",
    displayName: "Test Task",
    description: "A test agent task",
    playbook: "Follow the rubric.",
    lessons: ["Be concise", "Cite sources"],
    bestStrategy: { approach: "structured" },
    bestScore: 0.85,
    bestElo: 1600.0,
    hints: "Focus on clarity",
    taskPrompt: "Write a summary of the article.",
    judgeRubric: "Score based on accuracy and completeness.",
    exampleOutputs: makeExampleOutputs(),
    outputFormat: "free_text",
    ...overrides,
  });
}

describe("SkillPackage — Agent Task Markdown", () => {
  it("includes task section", () => {
    const md = makeAgentTaskPackage().toSkillMarkdown();
    expect(md).toContain("## Task");
    expect(md).toContain("Write a summary of the article.");
  });

  it("includes evaluation criteria", () => {
    const md = makeAgentTaskPackage().toSkillMarkdown();
    expect(md).toContain("## Evaluation Criteria");
    expect(md).toContain("Score based on accuracy and completeness.");
  });

  it("includes example outputs with details blocks", () => {
    const md = makeAgentTaskPackage().toSkillMarkdown();
    expect(md).toContain("## Example Outputs");
    expect(md).toContain("<details>");
    expect(md).toContain("<summary>");
    expect(md).toContain("</details>");
    expect(md).toContain("Great answer");
    expect(md).toContain("score: 0.95");
    expect(md).toContain("**Reasoning:**");
    expect(md).toContain("Thorough and accurate");
  });

  it("limits to three examples", () => {
    const outputs = [
      ...makeExampleOutputs(),
      { output: "Fourth", score: 0.10, reasoning: "Bad" },
    ];
    const md = makeAgentTaskPackage({ exampleOutputs: outputs }).toSkillMarkdown();
    expect(md).toContain("Weak answer");
    expect(md).not.toContain("Fourth");
  });

  it("uses text code block (not json) for strategy", () => {
    const md = makeAgentTaskPackage().toSkillMarkdown();
    expect(md).toContain("```\n{");
    expect(md).not.toContain("```json");
  });

  it("includes playbook", () => {
    const md = makeAgentTaskPackage().toSkillMarkdown();
    expect(md).toContain("## Playbook");
    expect(md).toContain("Follow the rubric.");
  });

  it("includes operational lessons", () => {
    const md = makeAgentTaskPackage().toSkillMarkdown();
    expect(md).toContain("## Operational Lessons");
    expect(md).toContain("- Be concise");
    expect(md).toContain("- Cite sources");
  });

  it("includes reference context when present", () => {
    const md = makeAgentTaskPackage({ referenceContext: "Domain knowledge" }).toSkillMarkdown();
    expect(md).toContain("## Reference Context");
    expect(md).toContain("Domain knowledge");
  });

  it("includes context preparation when present", () => {
    const md = makeAgentTaskPackage({ contextPreparation: "Load documents" }).toSkillMarkdown();
    expect(md).toContain("## Context Preparation");
    expect(md).toContain("Load documents");
  });

  it("omits optional sections when not present", () => {
    const md = makeAgentTaskPackage({
      referenceContext: null,
      contextPreparation: null,
      exampleOutputs: null,
    }).toSkillMarkdown();
    expect(md).not.toContain("## Reference Context");
    expect(md).not.toContain("## Context Preparation");
    expect(md).not.toContain("## Example Outputs");
  });
});

describe("SkillPackage — Game Scenario Markdown", () => {
  it("renders without agent task sections", () => {
    const pkg = new SkillPackage({
      scenarioName: "grid_ctf",
      displayName: "Grid CTF",
      description: "Capture the flag on a grid",
      playbook: "Move toward the flag.",
      lessons: ["Avoid corners"],
      bestStrategy: { x: 1, y: 2 },
      bestScore: 0.9,
      bestElo: 1700,
      hints: "Think ahead",
    });
    const md = pkg.toSkillMarkdown();
    expect(md).toContain("# Grid CTF");
    expect(md).toContain("## Playbook");
    expect(md).toContain("```json");
    expect(md).not.toContain("## Task");
    expect(md).not.toContain("## Evaluation Criteria");
  });
});

describe("SkillPackage — toDict", () => {
  it("serializes core fields", () => {
    const d = makeAgentTaskPackage().toDict();
    expect(d.scenario_name).toBe("test_task");
    expect(d.task_prompt).toBe("Write a summary of the article.");
    expect(d.judge_rubric).toBe("Score based on accuracy and completeness.");
    expect(d.example_outputs).toHaveLength(3);
  });

  it("omits null optional fields", () => {
    const d = makeAgentTaskPackage({
      taskPrompt: null,
      judgeRubric: null,
      referenceContext: null,
    }).toDict();
    expect("task_prompt" in d).toBe(false);
    expect("judge_rubric" in d).toBe(false);
    expect("reference_context" in d).toBe(false);
  });
});

describe("exportAgentTaskSkill", () => {
  it("creates package from opts", () => {
    const pkg = exportAgentTaskSkill({
      scenarioName: "summary_task",
      taskPrompt: "Summarize this",
      judgeRubric: "Check completeness",
      outputFormat: "free_text",
      playbook: "Read carefully, then summarize.",
      lessons: ["Keep it short"],
      bestOutputs: [{ output: "Good summary", score: 0.9, reasoning: "Concise" }],
    });
    expect(pkg.scenarioName).toBe("summary_task");
    expect(pkg.displayName).toBe("Summary Task");
    expect(pkg.bestScore).toBe(0.9);
    expect(pkg.taskPrompt).toBe("Summarize this");
    const md = pkg.toSkillMarkdown();
    expect(md).toContain("## Task");
  });

  it("handles empty bestOutputs", () => {
    const pkg = exportAgentTaskSkill({
      scenarioName: "empty_task",
      taskPrompt: "Do something",
      judgeRubric: "Check it",
      outputFormat: "free_text",
      playbook: "Try your best.",
      lessons: [],
      bestOutputs: [],
    });
    expect(pkg.bestScore).toBe(0.0);
    expect(pkg.exampleOutputs).toBeNull();
  });
});

describe("cleanLessons", () => {
  it("strips rollback lines", () => {
    const result = cleanLessons([
      "- Generation 3 ROLLBACK — score dropped",
      "- Keep outputs concise",
    ]);
    expect(result).toEqual(["Keep outputs concise"]);
  });

  it("strips raw JSON blobs", () => {
    const result = cleanLessons([
      '{"param_a": 0.5, "param_b": 0.3}',
      "Use structured format",
    ]);
    expect(result).toEqual(["Use structured format"]);
  });

  it("strips score parentheticals", () => {
    const result = cleanLessons([
      "- Improved accuracy (score=0.85, delta=+0.10, threshold=0.90)",
    ]);
    expect(result).toEqual(["Improved accuracy"]);
  });

  it("removes empty entries", () => {
    const result = cleanLessons(["", "  ", "Valid lesson"]);
    expect(result).toEqual(["Valid lesson"]);
  });
});
