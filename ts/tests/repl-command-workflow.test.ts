import { describe, expect, it } from "vitest";

import {
  buildReplSessionRequest,
  getReplUsageExitCode,
  parseReplPhase,
  planReplCommand,
  REPL_HELP_TEXT,
} from "../src/cli/repl-command-workflow.js";

describe("repl command workflow", () => {
  it("exposes stable help text", () => {
    expect(REPL_HELP_TEXT).toContain("autoctx repl");
    expect(REPL_HELP_TEXT).toContain("--phase <generate|revise>");
    expect(REPL_HELP_TEXT).toContain("--reference-context");
    expect(REPL_HELP_TEXT).toContain("--required-concept");
  });

  it("returns the right usage exit code", () => {
    expect(getReplUsageExitCode(true)).toBe(0);
    expect(getReplUsageExitCode(false)).toBe(1);
  });

  it("parses repl phase with generate fallback", () => {
    expect(parseReplPhase("revise")).toBe("revise");
    expect(parseReplPhase("generate")).toBe("generate");
    expect(parseReplPhase("anything-else")).toBe("generate");
  });

  it("rejects revise phase without current output", () => {
    expect(() =>
      planReplCommand(
        {
          scenario: undefined,
          prompt: "Task",
          rubric: "Rubric",
          output: undefined,
          phase: "revise",
          "reference-context": undefined,
          "required-concept": undefined,
          model: undefined,
          turns: undefined,
          "max-tokens": undefined,
          temperature: undefined,
          "max-stdout": undefined,
          "timeout-ms": undefined,
          "memory-mb": undefined,
        },
        null,
      ),
    ).toThrow("autoctx repl --phase revise requires -o/--output");
  });

  it("rejects missing prompt/rubric when no saved scenario is available", () => {
    expect(() =>
      planReplCommand(
        {
          scenario: undefined,
          prompt: undefined,
          rubric: undefined,
          output: undefined,
          phase: undefined,
          "reference-context": undefined,
          "required-concept": undefined,
          model: undefined,
          turns: undefined,
          "max-tokens": undefined,
          temperature: undefined,
          "max-stdout": undefined,
          "timeout-ms": undefined,
          "memory-mb": undefined,
        },
        null,
      ),
    ).toThrow(
      "Error: repl requires either --scenario <name> or both --prompt and --rubric.",
    );
  });

  it("merges saved scenario defaults with explicit overrides", () => {
    expect(
      planReplCommand(
        {
          scenario: "saved-scenario",
          prompt: undefined,
          rubric: "override rubric",
          output: "current output",
          phase: "revise",
          "reference-context": "override context",
          "required-concept": ["concept-b", "concept-a"],
          model: "override-model",
          turns: "8",
          "max-tokens": "4096",
          temperature: "0.4",
          "max-stdout": "9000",
          "timeout-ms": "12000",
          "memory-mb": "128",
        },
        {
          taskPrompt: "saved prompt",
          rubric: "saved rubric",
          referenceContext: "saved context",
          requiredConcepts: ["concept-a"],
        },
      ),
    ).toEqual({
      phase: "revise",
      taskPrompt: "saved prompt",
      rubric: "override rubric",
      currentOutput: "current output",
      referenceContext: "override context",
      requiredConcepts: ["concept-a", "concept-b"],
      requiresNoToolsProviderIsolation: false,
      config: {
        enabled: true,
        model: "override-model",
        maxTurns: 8,
        maxTokensPerTurn: 4096,
        temperature: 0.4,
        maxStdoutChars: 9000,
        codeTimeoutMs: 12000,
        memoryLimitMb: 128,
      },
    });
  });

  it("does not inherit judge-only grounding from a legacy saved AgentTaskSpec", () => {
    const plan = planReplCommand(
      {
        scenario: "legacy-scenario",
        "required-concept": ["explicit-candidate-concept"],
      },
      {
        taskPrompt: "Legacy public prompt",
        rubric: "Legacy judge rubric",
        referenceContext: "JUDGE_ONLY_REFERENCE",
        requiredConcepts: ["JUDGE_ONLY_CONCEPT"],
        agentTaskSpec: {
          taskPrompt: "Legacy public prompt",
          judgeRubric: "Legacy judge rubric",
          outputFormat: "free_text",
          judgeModel: "",
          referenceContext: "JUDGE_ONLY_REFERENCE",
          requiredConcepts: ["JUDGE_ONLY_CONCEPT"],
          maxRounds: 1,
          qualityThreshold: 0.9,
        },
      },
    );

    expect(plan.referenceContext).toBeUndefined();
    expect(plan.rubric).toBe("");
    expect(plan.requiredConcepts).toEqual(["explicit-candidate-concept"]);
    expect(plan.requiresNoToolsProviderIsolation).toBe(false);
  });

  it("inherits candidate-visible grounding from a structured v1 saved task", () => {
    const plan = planReplCommand(
      { scenario: "structured-scenario" },
      {
        taskPrompt: "Structured public prompt",
        rubric: "Structured rubric",
        referenceContext: "CANDIDATE_VISIBLE_REFERENCE",
        requiredConcepts: ["candidate-visible-concept"],
        agentTaskSpec: {
          improvementTaskContractVersion: 1,
          taskPrompt: "Structured public prompt",
          judgeRubric: "Structured rubric",
          outputFormat: "free_text",
          judgeModel: "",
          referenceContext: "CANDIDATE_VISIBLE_REFERENCE",
          requiredConcepts: ["candidate-visible-concept"],
          maxRounds: 1,
          qualityThreshold: 0.9,
        },
      },
    );

    expect(plan.referenceContext).toBe("CANDIDATE_VISIBLE_REFERENCE");
    expect(plan.requiredConcepts).toEqual(["candidate-visible-concept"]);
    expect(plan.requiresNoToolsProviderIsolation).toBe(true);
  });

  it("builds REPL session requests with provider/model wiring", () => {
    expect(
      buildReplSessionRequest({
        provider: { name: "deterministic" },
        model: "provider-model",
        plan: {
          phase: "generate",
          taskPrompt: "Task",
          rubric: "Rubric",
          currentOutput: undefined,
          referenceContext: "Context",
          requiredConcepts: ["concept-a"],
          requiresNoToolsProviderIsolation: false,
          config: {
            enabled: true,
            model: "override-model",
            maxTurns: 6,
            maxTokensPerTurn: 2048,
            temperature: 0.2,
            maxStdoutChars: 8192,
            codeTimeoutMs: 10000,
            memoryLimitMb: 64,
          },
        },
      }),
    ).toEqual({
      provider: { name: "deterministic" },
      model: "provider-model",
      config: {
        enabled: true,
        model: "override-model",
        maxTurns: 6,
        maxTokensPerTurn: 2048,
        temperature: 0.2,
        maxStdoutChars: 8192,
        codeTimeoutMs: 10000,
        memoryLimitMb: 64,
      },
      phase: "generate",
      taskPrompt: "Task",
      rubric: "Rubric",
      currentOutput: undefined,
      referenceContext: "Context",
      requiredConcepts: ["concept-a"],
      requiresNoToolsProviderIsolation: false,
    });
  });
});
