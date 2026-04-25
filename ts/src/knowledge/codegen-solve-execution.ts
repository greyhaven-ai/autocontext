import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import type { ScenarioFamilyName } from "../scenarios/families.js";
import type { GeneratedScenarioExecutionResult } from "../scenarios/codegen/executor.js";
import type { ExecutionValidationResult } from "../scenarios/codegen/index.js";
import type { SerializedSkillPackageDict } from "./package.js";
import { buildGeneratedScenarioSolvePackage } from "./solve-workflow.js";
import { SolveGenerationBudget } from "./solve-generation-budget.js";

export interface CodegenSolveExecutionResult {
  progress: number;
  result: SerializedSkillPackageDict;
}

export interface CodegenSolveDeps {
  generateSource?: (
    family: ScenarioFamilyName,
    spec: Record<string, unknown>,
    name: string,
  ) => Promise<{
    source: string;
    validation: ExecutionValidationResult;
  }>;
  executeScenario?: (opts: {
    source: string;
    family: ScenarioFamilyName;
    name: string;
    maxSteps?: number;
  }) => Promise<GeneratedScenarioExecutionResult>;
}

async function defaultGenerateSource(
  family: ScenarioFamilyName,
  spec: Record<string, unknown>,
  name: string,
): Promise<{
  source: string;
  validation: ExecutionValidationResult;
}> {
  const { generateAndValidateScenarioSource } = await import("../scenarios/codegen/index.js");
  return generateAndValidateScenarioSource(family, spec, name);
}

async function defaultExecuteScenario(opts: {
  source: string;
  family: ScenarioFamilyName;
  name: string;
  maxSteps?: number;
}): Promise<GeneratedScenarioExecutionResult> {
  const { executeGeneratedScenarioSource } = await import("../scenarios/codegen/executor.js");
  return executeGeneratedScenarioSource(opts);
}

function resolveMaxSteps(spec: Record<string, unknown>): number {
  const raw = spec.max_steps ?? spec.maxSteps;
  if (typeof raw === "number" && Number.isFinite(raw)) {
    return raw;
  }
  if (typeof raw === "string" && raw.trim()) {
    const parsed = Number(raw);
    if (!Number.isNaN(parsed)) {
      return parsed;
    }
  }
  return 20;
}

function persistGeneratedScenarioSource(opts: {
  knowledgeRoot: string;
  name: string;
  source: string;
}): string {
  const scenarioDir = join(opts.knowledgeRoot, "_custom_scenarios", opts.name);
  if (!existsSync(scenarioDir)) {
    mkdirSync(scenarioDir, { recursive: true });
  }
  writeFileSync(join(scenarioDir, "scenario.js"), opts.source, "utf-8");
  return scenarioDir;
}

export async function executeCodegenSolve(opts: {
  knowledgeRoot: string;
  created: {
    name: string;
    family: ScenarioFamilyName;
    spec: Record<string, unknown>;
  };
  deps?: CodegenSolveDeps;
  generationTimeBudgetSeconds?: number | null;
}): Promise<CodegenSolveExecutionResult> {
  const generateSource = opts.deps?.generateSource ?? defaultGenerateSource;
  const executeScenario = opts.deps?.executeScenario ?? defaultExecuteScenario;
  const timeBudget = new SolveGenerationBudget({
    scenarioName: opts.created.name,
    budgetSeconds: opts.generationTimeBudgetSeconds,
  });

  timeBudget.check("source generation");
  const { source, validation } = await generateSource(
    opts.created.family,
    opts.created.spec,
    opts.created.name,
  );
  timeBudget.check("source generation");

  persistGeneratedScenarioSource({
    knowledgeRoot: opts.knowledgeRoot,
    name: opts.created.name,
    source,
  });
  timeBudget.check("source persistence");

  timeBudget.check("scenario execution");
  const execution = await executeScenario({
    source,
    family: opts.created.family,
    name: opts.created.name,
    maxSteps: resolveMaxSteps(opts.created.spec),
  });
  timeBudget.check("scenario execution");

  return {
    progress: execution.stepsExecuted,
    result: buildGeneratedScenarioSolvePackage({
      scenarioName: opts.created.name,
      family: opts.created.family,
      description: String(opts.created.spec.description ?? `Generated ${opts.created.family} scenario`),
      score: execution.score,
      reasoning: execution.reasoning,
      dimensionScores: execution.dimensionScores,
      records: execution.records,
      stepsExecuted: execution.stepsExecuted,
      validation: {
        durationMs: validation.durationMs,
        executedMethods: validation.executedMethods,
      },
    }),
  };
}
