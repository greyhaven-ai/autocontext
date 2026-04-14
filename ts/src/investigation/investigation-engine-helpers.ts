import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { getScenarioTypeMarker } from "../scenarios/families.js";
import type {
  InvestigationRequest,
  InvestigationResult,
} from "./investigation-contracts.js";

export function generateInvestigationId(): string {
  return `inv_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

export function deriveInvestigationName(description: string): string {
  return description.toLowerCase().replace(/[^a-z0-9\s]/g, "").split(/\s+/)
    .filter((word) => word.length > 2).slice(0, 4).join("_") || "investigation";
}

export function parseInvestigationJson(text: string): Record<string, unknown> | null {
  const trimmed = text.trim();
  try {
    return JSON.parse(trimmed) as Record<string, unknown>;
  } catch {
    // continue
  }

  const start = trimmed.indexOf("{");
  const end = trimmed.lastIndexOf("}");
  if (start !== -1 && end > start) {
    try {
      return JSON.parse(trimmed.slice(start, end + 1)) as Record<string, unknown>;
    } catch {
      // continue
    }
  }

  return null;
}

export function normalizePositiveInteger(value: number | undefined): number | undefined {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return undefined;
  }
  const rounded = Math.floor(value);
  return rounded > 0 ? rounded : undefined;
}

export function persistInvestigationArtifacts(
  knowledgeRoot: string,
  name: string,
  spec: Record<string, unknown>,
  source: string,
): string {
  const investigationDir = join(knowledgeRoot, "_investigations", name);
  if (!existsSync(investigationDir)) {
    mkdirSync(investigationDir, { recursive: true });
  }
  writeFileSync(
    join(investigationDir, "spec.json"),
    JSON.stringify({ name, family: "investigation", ...spec }, null, 2),
    "utf-8",
  );
  writeFileSync(join(investigationDir, "scenario.js"), source, "utf-8");
  writeFileSync(
    join(investigationDir, "scenario_type.txt"),
    getScenarioTypeMarker("investigation"),
    "utf-8",
  );
  return investigationDir;
}

export function buildFailedInvestigationResult(
  id: string,
  name: string,
  request: InvestigationRequest,
  errors: string[],
): InvestigationResult {
  return {
    id,
    name,
    family: "investigation",
    status: "failed",
    description: request.description,
    question: request.description,
    hypotheses: [],
    evidence: [],
    conclusion: { bestExplanation: "", confidence: 0, limitations: errors },
    unknowns: [],
    recommendedNextSteps: [],
    stepsExecuted: 0,
    artifacts: { investigationDir: "" },
    error: errors.join("; "),
  };
}
