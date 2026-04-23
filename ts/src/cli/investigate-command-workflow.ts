import type {
  InvestigationRequest,
  InvestigationResult,
} from "../investigation/engine.js";
import {
  captureInvestigationBrowserContext,
  type InvestigationBrowserContextSettingsLike,
} from "../investigation/browser-context.js";
import { deriveInvestigationName } from "../investigation/investigation-engine-helpers.js";

export const INVESTIGATE_HELP_TEXT = `autoctx investigate — run a plain-language investigation

Usage: autoctx investigate --description "..." [options]

Options:
  -d, --description <text>   Plain-language problem to investigate (required)
  --max-steps <N>            Maximum investigation steps (default: 8)
  --hypotheses <N>           Maximum hypotheses to generate (default: 5)
  --save-as <name>           Name for the saved investigation
  --browser-url <url>        Capture a browser snapshot from the given URL and use it as evidence
  --json                     Output as JSON
  -h, --help                 Show this help

Examples:
  autoctx investigate -d "why did conversion drop after Tuesday's release"
  autoctx investigate -d "intermittent CI failures" --max-steps 12 --json
  autoctx investigate -d "model benchmark improved but real performance fell" --save-as benchmark_rca
  autoctx investigate -d "checkout is failing in prod" --browser-url https://status.example.com`;

export interface InvestigateCommandValues {
  description?: string;
  "max-steps"?: string;
  hypotheses?: string;
  "save-as"?: string;
  "browser-url"?: string;
  json?: boolean;
}

export interface InvestigateCommandEngine {
  run(request: InvestigationRequest): Promise<InvestigationResult>;
}

export interface PrepareInvestigateRequestDependencies {
  captureBrowserContext: typeof captureInvestigationBrowserContext;
}

const DEFAULT_PREPARE_DEPENDENCIES: PrepareInvestigateRequestDependencies = {
  captureBrowserContext: captureInvestigationBrowserContext,
};

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

export async function prepareInvestigateRequest(
  opts: {
    values: InvestigateCommandValues;
    settings: InvestigationBrowserContextSettingsLike;
  },
  dependencies: Partial<PrepareInvestigateRequestDependencies> = {},
): Promise<InvestigationRequest> {
  const resolved = {
    ...DEFAULT_PREPARE_DEPENDENCIES,
    ...dependencies,
  };
  const request = planInvestigateCommand(opts.values);
  const browserUrl = opts.values["browser-url"];
  if (!browserUrl) {
    return request;
  }

  return {
    ...request,
    browserContext: await resolved.captureBrowserContext({
      settings: opts.settings,
      browserUrl,
      investigationName: request.saveAs ?? deriveInvestigationName(request.description),
    }),
  };
}

export async function executeInvestigateCommandWorkflow(opts: {
  values: InvestigateCommandValues;
  engine: InvestigateCommandEngine;
  request?: InvestigationRequest;
}): Promise<InvestigationResult> {
  const request = opts.request ?? planInvestigateCommand(opts.values);
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
