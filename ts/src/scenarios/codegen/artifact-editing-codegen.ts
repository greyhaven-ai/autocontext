/**
 * Artifact-editing family codegen (AC-436).
 * Mirrors Python's autocontext/scenarios/custom/artifact_editing_codegen.py.
 */

export function generateArtifactEditingSource(
  spec: Record<string, unknown>,
  name: string,
): string {
  const description = String(spec.description ?? "");
  const rubric = String(spec.rubric ?? spec.judgeRubric ?? "");
  const artifacts = (spec.artifacts ?? spec.initial_artifacts ?? []) as Array<{
    name: string;
    content: string;
    format: string;
    validationRules?: string[];
  }>;
  const editInstructions = String(spec.edit_instructions ?? spec.editInstructions ?? "Edit the artifacts according to the task.");

  const artifactsJson = JSON.stringify(
    artifacts.map((a) => ({
      name: a.name,
      content: a.content,
      format: a.format ?? "text",
      validationRules: a.validationRules ?? [],
    })),
    null,
    2,
  );

  return `// Generated artifact_editing scenario: ${name}
const ARTIFACTS = ${artifactsJson};

const scenario = {
  name: ${JSON.stringify(name)},

  describeTask() {
    return ${JSON.stringify(description)};
  },

  getRubric() {
    return ${JSON.stringify(rubric)};
  },

  initialArtifacts() {
    return ARTIFACTS.map(a => ({ ...a }));
  },

  getEditPrompt(artifacts, state) {
    return ${JSON.stringify(editInstructions)};
  },

  validateArtifact(artifact) {
    const spec = ARTIFACTS.find(a => a.name === artifact.name);
    if (!spec) return { valid: false, errors: ["unknown artifact: " + artifact.name] };
    const errors = [];
    for (const rule of spec.validationRules || []) {
      if (!artifact.content.includes(rule)) {
        errors.push("validation rule not satisfied: " + rule);
      }
    }
    return { valid: errors.length === 0, errors };
  },

  initialState() {
    return { artifacts: ARTIFACTS.map(a => ({ ...a })), round: 0 };
  },

  evaluateOutput(editedArtifacts, state) {
    let totalValid = 0;
    const results = [];
    for (const artifact of editedArtifacts || []) {
      const validation = scenario.validateArtifact(artifact);
      if (validation.valid) totalValid++;
      results.push({ name: artifact.name, ...validation });
    }
    const score = ARTIFACTS.length > 0 ? totalValid / ARTIFACTS.length : 0;
    return {
      score: Math.round(score * 10000) / 10000,
      reasoning: totalValid + " of " + ARTIFACTS.length + " artifacts valid.",
      dimensionScores: { validity: Math.round(score * 10000) / 10000 },
    };
  },
};

module.exports = { scenario };
`;
}
