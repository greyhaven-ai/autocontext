export const AGENT_TASK_SCENARIO_TEMPLATE = String.raw`// Generated agent_task scenario: __SCENARIO_NAME_COMMENT__
const scenario = {
  name: __SCENARIO_NAME__,

  getTaskPrompt(state) {
    return __TASK_PROMPT__;
  },

  getRubric() {
    return __JUDGE_RUBRIC__;
  },

  describeTask() {
    return __DESCRIPTION__;
  },

  initialState() {
    return {
      outputFormat: __OUTPUT_FORMAT__,
      maxRounds: __MAX_ROUNDS__,
      qualityThreshold: __QUALITY_THRESHOLD__,
      currentRound: 0,
    };
  },

  async evaluateOutput(output, state) {
    // Basic keyword-based evaluation for deterministic testing.
    // In production, this is replaced by LLM judge evaluation.
    const rubric = __JUDGE_RUBRIC__;
    const rubricWords = rubric.toLowerCase().split(/\W+/).filter(w => w.length > 3);
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
