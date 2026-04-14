import type {
  InvestigationRequest,
  InvestigationResult,
} from "../investigation/engine.js";

export const INVESTIGATE_HELP_TEXT = `autoctx investigate — run a plain-language investigation

Usage: autoctx investigate --description "..." [options]

Options:
  -d, --description <text>   Plain-language problem to investigate (required)
  --max-steps <N>            Maximum investigation steps (default: 8)
  --hypotheses <N>           Maximum hypotheses to generate (default: 5)
  --save-as <name>           Name for the saved investigation
  --json                     Output as JSON
  -h, --help                 Show this help

Examples:
  autoctx investigate -d "why did conversion drop after Tuesday's release"
  autoctx investigate -d "intermittent CI failures" --max-steps 12 --json
  autoctx investigate -d "model benchmark improved but real performance fell" --save-as benchmark_rca`;

export interface InvestigateCommandValues {
  description?: string;
  "max-steps"?: string;
  hypotheses?: string;
  "save-as"?: string;
  json?: boolean;
}

export interface InvestigateCommandEngine {
  run(request: InvestigationRequest): Promise<InvestigationResult>;
}

export function planInvestigateCommand(
  values: InvestigateCommandValues,
): InvestigationRequest {
  if (!values.description) {
    throw new Error(
      "Error: --description is required. Run 'autoctx investigate --help' for usage.",
    );
  }

  return {
    description: values.description,
    maxSteps: values["max-steps"]
      ? Number.parseInt(values["max-steps"], 10)
      : undefined,
    maxHypotheses: values.hypotheses
      ? Number.parseInt(values.hypotheses, 10)
      : undefined,
    saveAs: values["save-as"],
  };
}

export async function executeInvestigateCommandWorkflow(opts: {
  values: InvestigateCommandValues;
  engine: InvestigateCommandEngine;
}): Promise<InvestigationResult> {
  const request = planInvestigateCommand(opts.values);
  return opts.engine.run(request);
}

export function renderInvestigationSuccess(
  result: InvestigationResult,
): string {
  const lines = [
    `Investigation: ${result.name}`,
    `Question: ${result.question}`,
    "",
    "Hypotheses:",
  ];

  for (const hypothesis of result.hypotheses) {
    const icon =
      hypothesis.status === "supported"
        ? "✓"
        : hypothesis.status === "contradicted"
          ? "✗"
          : "?";
    lines.push(
      `  ${icon} ${hypothesis.statement} (confidence: ${hypothesis.confidence.toFixed(2)}, ${hypothesis.status})`,
    );
  }

  lines.push(
    "",
    `Conclusion: ${result.conclusion.bestExplanation}`,
    `Confidence: ${result.conclusion.confidence.toFixed(2)}`,
  );

  if (result.unknowns.length > 0) {
    lines.push("", "Unknowns:");
    for (const unknown of result.unknowns) {
      lines.push(`  - ${unknown}`);
    }
  }

  if (result.recommendedNextSteps.length > 0) {
    lines.push("", "Next steps:");
    for (const step of result.recommendedNextSteps) {
      lines.push(`  → ${step}`);
    }
  }

  lines.push("", `Artifacts: ${result.artifacts.investigationDir}`);
  return lines.join("\n");
}
