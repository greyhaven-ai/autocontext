export const ARTIFACT_EDITING_SCENARIO_TEMPLATE = String.raw`// Generated artifact_editing scenario: __SCENARIO_NAME_COMMENT__
const ARTIFACTS = __ARTIFACTS__;

const scenario = {
  name: __SCENARIO_NAME__,

  describeTask() {
    return __DESCRIPTION__;
  },

  getRubric() {
    return __RUBRIC__;
  },

  initialArtifacts() {
    return ARTIFACTS.map((artifact) => ({ ...artifact }));
  },

  getEditPrompt(artifacts, state) {
    return __EDIT_INSTRUCTIONS__;
  },

  validateArtifact(artifact) {
    const spec = ARTIFACTS.find((candidate) => candidate.name === artifact.name);
    if (!spec) {
      return { valid: false, errors: ["unknown artifact: " + artifact.name] };
    }
    const errors = [];
    for (const rule of spec.validationRules || []) {
      if (!artifact.content.includes(rule)) {
        errors.push("validation rule not satisfied: " + rule);
      }
    }
    return { valid: errors.length === 0, errors };
  },

  initialState() {
    return { artifacts: ARTIFACTS.map((artifact) => ({ ...artifact })), round: 0 };
  },

  evaluateOutput(editedArtifacts, state) {
    let totalValid = 0;
    const results = [];
    for (const artifact of editedArtifacts || []) {
      const validation = scenario.validateArtifact(artifact);
      if (validation.valid) {
        totalValid++;
      }
      results.push({ name: artifact.name, ...validation });
    }
    const score = ARTIFACTS.length > 0 ? totalValid / ARTIFACTS.length : 0;
    return {
      score: Math.round(score * 10000) / 10000,
      reasoning: totalValid + " of " + ARTIFACTS.length + " artifacts valid.",
      dimensionScores: {
        validity: Math.round(score * 10000) / 10000,
      },
    };
  },
};

module.exports = { scenario };
`;
