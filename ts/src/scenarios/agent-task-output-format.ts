import type { AgentTaskSpec } from "./agent-task-spec.js";

/**
 * Candidate-facing output constraints derived from the persisted agent-task
 * contract. The contract currently names the format but does not carry a
 * concrete JSON schema or programming language, so keep these instructions
 * deliberately narrow and truthful.
 */
export function buildAgentTaskOutputFormatBlock(
  outputFormat: AgentTaskSpec["outputFormat"],
): string {
  switch (outputFormat) {
    case "json_schema":
      return [
        "## Output Format",
        "Return only valid JSON. Do not include Markdown fences, comments, or explanatory prose.",
      ].join("\n");
    case "code":
      return [
        "## Output Format",
        "Return only the requested code. Do not include Markdown fences or explanatory prose.",
      ].join("\n");
    case "free_text":
      return "";
  }
}

/**
 * Enforce the only output contract that can be checked without inventing a
 * schema. Structured-v1 `json_schema` tasks require syntactically valid JSON,
 * while accepting every JSON value shape (object, array, or scalar).
 */
export function assertAgentTaskOutputFormat(opts: {
  improvementTaskContractVersion?: 1;
  outputFormat: AgentTaskSpec["outputFormat"];
  output: string;
  artifactLabel: string;
}): void {
  if (
    opts.improvementTaskContractVersion !== 1
    || opts.outputFormat !== "json_schema"
  ) {
    return;
  }

  try {
    JSON.parse(opts.output);
  } catch {
    throw new Error(
      `Structured-v1 agent-task ${opts.artifactLabel} must be valid JSON because output_format is json_schema`,
    );
  }
}
