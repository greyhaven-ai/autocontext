/**
 * Agent-task family codegen — generates JS source from an AgentTaskSpec (AC-436).
 * Mirrors Python's autocontext/scenarios/custom/agent_task_codegen.py.
 */

export function generateAgentTaskSource(
  spec: Record<string, unknown>,
  name: string,
): string {
  const taskPrompt = String(spec.taskPrompt ?? spec.task_prompt ?? "");
  const judgeRubric = String(spec.judgeRubric ?? spec.judge_rubric ?? spec.rubric ?? "");
  const description = String(spec.description ?? `Agent task: ${name}`);
  const outputFormat = String(spec.outputFormat ?? spec.output_format ?? "free_text");
  const maxRounds = Number(spec.maxRounds ?? spec.max_rounds ?? 1);
  const qualityThreshold = Number(spec.qualityThreshold ?? spec.quality_threshold ?? 0.9);

  return `// Generated agent_task scenario: ${name}
const scenario = {
  name: ${JSON.stringify(name)},

  getTaskPrompt(state) {
    return ${JSON.stringify(taskPrompt)};
  },

  getRubric() {
    return ${JSON.stringify(judgeRubric)};
  },

  describeTask() {
    return ${JSON.stringify(description)};
  },

  initialState() {
    return {
      outputFormat: ${JSON.stringify(outputFormat)},
      maxRounds: ${maxRounds},
      qualityThreshold: ${qualityThreshold},
      currentRound: 0,
    };
  },

  async evaluateOutput(output, state) {
    // Basic keyword-based evaluation for deterministic testing.
    // In production, this is replaced by LLM judge evaluation.
    const rubric = ${JSON.stringify(judgeRubric)};
    const rubricWords = rubric.toLowerCase().split(/\\W+/).filter(w => w.length > 3);
    const outputLower = (output || "").toLowerCase();
    const matches = rubricWords.filter(w => outputLower.includes(w)).length;
    const score = rubricWords.length > 0 ? Math.min(1.0, matches / Math.max(rubricWords.length * 0.3, 1)) : 0.5;
    return {
      score: Math.round(score * 10000) / 10000,
      reasoning: "Matched " + matches + " of " + rubricWords.length + " rubric keywords.",
      dimensionScores: {
        relevance: Math.round(score * 10000) / 10000,
        completeness: Math.round(Math.min(1.0, (output || "").length / 200) * 10000) / 10000,
      },
    };
  },

  getRevisionPrompt(output, feedback) {
    return "Revise your previous output based on this feedback: " + (feedback || "Improve quality.");
  },
};

module.exports = { scenario };
`;
}
