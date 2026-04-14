import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { generateScenarioSource } from "../scenarios/codegen/registry.js";
import { validateGeneratedScenario } from "../scenarios/codegen/execution-validator.js";
import type { ScenarioFamilyName } from "../scenarios/families.js";
import { healSpec } from "../scenarios/spec-auto-heal.js";
import type { LLMProvider } from "../types/index.js";
import { loadPersistedSimulationSpec } from "./artifact-store.js";

export interface BuiltSimulationVariant {
  spec: Record<string, unknown>;
  source: string;
}

export interface ReplayVariant extends BuiltSimulationVariant {
  variables: Record<string, unknown>;
}

export interface BuildSimulationVariantOpts {
  provider: LLMProvider;
  description: string;
  family: ScenarioFamilyName;
  name: string;
  variables?: Record<string, unknown>;
}

export interface LoadReplaySimulationVariantOpts {
  scenarioDir: string;
  family: ScenarioFamilyName;
  name: string;
  variables: Record<string, unknown>;
  regenerate: boolean;
}

export function parseSimulationSpecJson(
  text: string,
): Record<string, unknown> | null {
  const trimmed = text.trim();
  try {
    return JSON.parse(trimmed);
  } catch {
    /* continue */
  }
  const start = trimmed.indexOf("{");
  const end = trimmed.lastIndexOf("}");
  if (start !== -1 && end > start) {
    try {
      return JSON.parse(trimmed.slice(start, end + 1));
    } catch {
      /* continue */
    }
  }
  return null;
}

export async function buildSimulationSpec(opts: {
  provider: LLMProvider;
  description: string;
  family: ScenarioFamilyName;
  variables?: Record<string, unknown>;
}): Promise<Record<string, unknown>> {
  const serializedVariables =
    opts.variables && Object.keys(opts.variables).length > 0
      ? JSON.stringify(opts.variables, null, 2)
      : "";
  const systemPrompt = `You are a simulation designer. Given a plain-language description, produce a ${opts.family} spec as a JSON object.

Required fields:
- description: scenario summary
- environment_description: system context
- initial_state_description: starting state
- success_criteria: array of strings
- failure_modes: array of strings
- max_steps: positive integer
- actions: array of {name, description, parameters, preconditions, effects}
${opts.family === "operator_loop" ? "- escalation_policy: {escalation_threshold, max_escalations}" : ""}
${opts.family === "coordination" ? "- workers: array of {worker_id, role} with at least 2 workers" : ""}

${
  serializedVariables
    ? `Incorporate these requested simulation parameters directly into the returned spec so they materially change execution when they change:
${serializedVariables}

Prefer mapping them into native fields like max_steps, escalation_policy, workers, action parameters, environment details, or other family-appropriate controls. If a parameter does not cleanly fit a native field, preserve it under simulation_variables.`
    : ""
}

Output ONLY the JSON object, no markdown fences.`;

  const result = await opts.provider.complete({
    systemPrompt,
    userPrompt: `Simulation request: ${opts.description}${serializedVariables ? `\n\nRequested parameters:\n${serializedVariables}` : ""}`,
  });

  const parsed = parseSimulationSpecJson(result.text);
  if (!parsed) {
    throw new Error("Simulation spec generation did not return valid JSON");
  }
  return parsed;
}

export async function buildSimulationVariant(
  opts: BuildSimulationVariantOpts,
): Promise<BuiltSimulationVariant> {
  const rawSpec = await buildSimulationSpec(opts);
  const healedSpec = applySimulationVariableOverrides(
    healSpec(rawSpec, opts.family),
    opts.family,
    opts.variables,
  );
  const source = generateScenarioSource(opts.family, healedSpec, opts.name);
  const validation = await validateGeneratedScenario(
    source,
    opts.family,
    opts.name,
  );
  if (!validation.valid) {
    throw new Error(validation.errors.join("; "));
  }
  return { spec: healedSpec, source };
}

export function applySimulationVariableOverrides(
  spec: Record<string, unknown>,
  family: ScenarioFamilyName,
  variables?: Record<string, unknown>,
): Record<string, unknown> {
  if (!variables || Object.keys(variables).length === 0) {
    return spec;
  }

  const next: Record<string, unknown> = { ...spec };
  const passthrough: Record<string, unknown> = {};

  for (const [key, value] of Object.entries(variables)) {
    switch (key) {
      case "max_steps":
      case "maxSteps": {
        const maxSteps = Number(value);
        if (Number.isFinite(maxSteps) && maxSteps > 0) {
          next.max_steps = Math.floor(maxSteps);
        }
        break;
      }
      case "escalation_threshold":
      case "escalationThreshold": {
        if (family === "operator_loop") {
          const policy = {
            ...((next.escalation_policy as Record<string, unknown>) ?? {}),
          };
          policy.escalation_threshold = value;
          next.escalation_policy = policy;
        } else {
          passthrough[key] = value;
        }
        break;
      }
      case "max_escalations":
      case "maxEscalations": {
        const maxEscalations = Number(value);
        if (
          family === "operator_loop" &&
          Number.isFinite(maxEscalations) &&
          maxEscalations > 0
        ) {
          const policy = {
            ...((next.escalation_policy as Record<string, unknown>) ?? {}),
          };
          policy.max_escalations = Math.floor(maxEscalations);
          next.escalation_policy = policy;
        } else {
          passthrough[key] = value;
        }
        break;
      }
      case "worker_count":
      case "workerCount": {
        const workerCount = Number(value);
        if (
          family === "coordination" &&
          Number.isFinite(workerCount) &&
          workerCount >= 2
        ) {
          const existingWorkers = Array.isArray(next.workers)
            ? [...(next.workers as Array<Record<string, unknown>>)]
            : [];
          const normalizedCount = Math.floor(workerCount);
          const workers = existingWorkers.slice(0, normalizedCount);
          while (workers.length < normalizedCount) {
            workers.push({
              worker_id: `worker_${workers.length + 1}`,
              role: `Worker ${workers.length + 1}`,
            });
          }
          next.workers = workers;
        } else {
          passthrough[key] = value;
        }
        break;
      }
      default:
        passthrough[key] = value;
    }
  }

  if (Object.keys(passthrough).length > 0) {
    const existingVariables =
      next.simulation_variables && typeof next.simulation_variables === "object"
        ? (next.simulation_variables as Record<string, unknown>)
        : {};
    next.simulation_variables = { ...existingVariables, ...passthrough };
  }

  return next;
}

export async function loadReplaySimulationVariant(
  opts: LoadReplaySimulationVariantOpts,
): Promise<ReplayVariant> {
  const sourcePath = join(opts.scenarioDir, "scenario.js");
  const specPath = join(opts.scenarioDir, "spec.json");
  const savedSpec = loadPersistedSimulationSpec(specPath);

  if (!opts.regenerate && existsSync(sourcePath)) {
    return {
      spec: savedSpec ?? {},
      source: readFileSync(sourcePath, "utf-8"),
      variables: opts.variables,
    };
  }

  if (!savedSpec) {
    throw new Error(`Saved simulation spec not found at ${specPath}`);
  }

  const spec = applySimulationVariableOverrides(savedSpec, opts.family, opts.variables);
  const source = generateScenarioSource(opts.family, spec, opts.name);
  const validation = await validateGeneratedScenario(source, opts.family, opts.name);
  if (!validation.valid) {
    throw new Error(validation.errors.join("; "));
  }

  return {
    spec,
    source,
    variables: opts.variables,
  };
}
