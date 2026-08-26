import type { AgentTaskSpec } from "../scenarios/agent-task-spec.js";
import {
  overrideSavedAgentTaskSpec,
  requiresNativeAgentTaskExecution,
} from "../scenarios/saved-agent-task-routing.js";

export const JUDGE_HELP_TEXT = `autoctx judge — One-shot evaluation of output against a rubric

Usage: autoctx judge [options]

Options:
  -s, --scenario <name>  Use a saved custom scenario (provides prompt + rubric)
  -p, --prompt <text>    Task prompt (what was asked of the agent)
  -o, --output <text>    Agent output to evaluate (required)
  -r, --rubric <text>    Evaluation rubric/criteria
  --from-stdin           Read a pre-computed evaluation JSON from stdin

Provide either --scenario or both --prompt and --rubric.
Use --from-stdin to accept a pre-computed evaluation (agent-as-judge pattern).

Examples:
  autoctx judge -p "Summarize this doc" -o "The doc covers..." -r "Score clarity 0-1"
  autoctx judge -s my_saved_task -o "Agent response here"
  echo '{"score":0.85,"reasoning":"Good"}' | autoctx judge --from-stdin

See also: improve, queue, run`;

export interface JudgeCommandValues {
  scenario?: string;
  prompt?: string;
  output?: string;
  rubric?: string;
  "from-stdin"?: boolean;
  help?: boolean;
}

type JudgeParseMethod =
  | "raw_json"
  | "code_block"
  | "markers"
  | "plaintext"
  | "none"
  | "delegated"
  | "callback";

export interface JudgeCommandResult {
  score: number;
  reasoning: string;
  dimensionScores: Record<string, number>;
  authoritativeParseFailed: boolean;
  judgeFailed: boolean;
  parseMethod?: JudgeParseMethod;
  source?: string;
}

export function getJudgeUsageExitCode(values: JudgeCommandValues): 0 | 1 | null {
  if (values.help) return 0;
  if (
    !values["from-stdin"] &&
    (!values.output || (!values.scenario && (!values.prompt || !values.rubric)))
  ) {
    return 1;
  }
  return null;
}

export function parseDelegatedJudgeInput(
  input: string,
): JudgeCommandResult & { source: "delegated" } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(input.trim());
  } catch {
    throw new Error("Invalid JSON on stdin");
  }
  if (!isJsonObject(parsed)) {
    throw new Error("Invalid judge result: expected a JSON object");
  }

  const score = parsed.score;
  if (typeof score !== "number" || !Number.isFinite(score) || score < 0 || score > 1) {
    throw new Error("Invalid score: must be a number between 0 and 1");
  }
  if (parsed.reasoning !== undefined && typeof parsed.reasoning !== "string") {
    throw new Error("Invalid reasoning: must be a string");
  }
  const authoritativeParseFailed = parseOptionalBoolean(
    parsed.authoritativeParseFailed,
    "authoritativeParseFailed",
  );
  const judgeFailed = parseOptionalBoolean(parsed.judgeFailed, "judgeFailed");
  const parseMethod = parseJudgeParseMethod(parsed.parseMethod);
  const normalized = normalizeJudgeCommandResult({
    score,
    reasoning: parsed.reasoning ?? "",
    dimensionScores: parseDimensionScores(parsed.dimensions ?? parsed.dimensionScores),
    authoritativeParseFailed,
    judgeFailed,
    parseMethod,
  });

  return {
    ...normalized,
    source: "delegated",
  };
}

export function planJudgeCommand(
  values: JudgeCommandValues,
  savedScenario: {
    name?: string;
    agentTaskSpec?: AgentTaskSpec;
    taskPrompt?: string;
    rubric?: string;
    referenceContext?: string;
    requiredConcepts?: string[];
    calibrationExamples?: Record<string, unknown>[];
  } | null,
): {
  taskPrompt: string;
  rubric: string;
  agentOutput: string;
  referenceContext?: string;
  requiredConcepts?: string[];
  calibrationExamples?: Record<string, unknown>[];
  nativeAgentTask?: { name: string; spec: AgentTaskSpec };
} {
  const taskPrompt = values.prompt ?? savedScenario?.taskPrompt;
  const rubric = values.rubric ?? savedScenario?.rubric;
  const agentOutput = values.output;

  if (!taskPrompt || !rubric || !agentOutput) {
    throw new Error(
      "Error: judge requires either --scenario <name> or both --prompt and --rubric.",
    );
  }

  const nativeAgentTask =
    savedScenario?.agentTaskSpec && requiresNativeAgentTaskExecution(savedScenario.agentTaskSpec)
      ? {
          name: savedScenario.name ?? values.scenario ?? "saved-agent-task",
          spec: overrideSavedAgentTaskSpec(savedScenario.agentTaskSpec, {
            taskPrompt: values.prompt,
            judgeRubric: values.rubric,
          }),
        }
      : undefined;

  return {
    taskPrompt,
    rubric,
    agentOutput,
    referenceContext: savedScenario?.referenceContext,
    requiredConcepts: savedScenario?.requiredConcepts,
    calibrationExamples: savedScenario?.calibrationExamples,
    ...(nativeAgentTask ? { nativeAgentTask } : {}),
  };
}

export async function executeJudgeCommandWorkflow(opts: {
  plan: {
    taskPrompt: string;
    rubric: string;
    agentOutput: string;
    referenceContext?: string;
    requiredConcepts?: string[];
    calibrationExamples?: Record<string, unknown>[];
    nativeAgentTask?: { name: string; spec: AgentTaskSpec };
  };
  provider: unknown;
  model?: string;
  createJudge: (args: {
    provider: unknown;
    model?: string;
    rubric: string;
  }) => {
    evaluate(args: {
      taskPrompt: string;
      agentOutput: string;
      referenceContext?: string;
      requiredConcepts?: string[];
      calibrationExamples?: Record<string, unknown>[];
    }): Promise<{
      score: number;
      reasoning: string;
      dimensionScores: Record<string, number>;
      authoritativeParseFailed?: boolean;
      judgeFailed?: boolean;
      parseMethod?: JudgeParseMethod;
    }>;
  };
  createAgentTask?: (args: {
    name: string;
    spec: AgentTaskSpec;
    provider: unknown;
    model?: string;
  }) => {
    initialState(seed?: number): Record<string, unknown>;
    prepareContext?(state: Record<string, unknown>): Promise<Record<string, unknown>>;
    validateContext?(state: Record<string, unknown>): string[];
    evaluateOutput(
      output: string,
      state: Record<string, unknown>,
      evalOpts?: {
        referenceContext?: string;
        requiredConcepts?: string[];
        calibrationExamples?: Record<string, unknown>[];
      },
    ): Promise<{
      score: number;
      reasoning: string;
      dimensionScores: Record<string, number>;
      authoritativeParseFailed?: boolean;
      judgeFailed?: boolean;
      parseMethod?: JudgeParseMethod;
    }>;
  };
}): Promise<JudgeCommandResult> {
  if (opts.plan.nativeAgentTask) {
    if (!opts.createAgentTask) {
      throw new Error(
        "Saved structured task requires native dual-context evaluation; evaluator-only evidence was not sent to a generic judge",
      );
    }
    const task = opts.createAgentTask({
      ...opts.plan.nativeAgentTask,
      provider: opts.provider,
      model: opts.model,
    });
    let state = task.initialState();
    if (task.prepareContext) {
      state = await task.prepareContext(state);
    }
    const contextErrors = task.validateContext?.(state) ?? [];
    if (contextErrors.length > 0) {
      throw new Error(`agent_task context preparation failed: ${contextErrors.join("; ")}`);
    }
    const result = await task.evaluateOutput(opts.plan.agentOutput, state, {
      referenceContext: opts.plan.nativeAgentTask.spec.referenceContext ?? undefined,
      requiredConcepts: opts.plan.nativeAgentTask.spec.requiredConcepts ?? undefined,
      calibrationExamples: opts.plan.nativeAgentTask.spec.calibrationExamples ?? undefined,
    });
    return normalizeJudgeCommandResult(result);
  }

  const judge = opts.createJudge({
    provider: opts.provider,
    model: opts.model,
    rubric: opts.plan.rubric,
  });

  const result = await judge.evaluate({
    taskPrompt: opts.plan.taskPrompt,
    agentOutput: opts.plan.agentOutput,
    referenceContext: opts.plan.referenceContext,
    requiredConcepts: opts.plan.requiredConcepts,
    calibrationExamples: opts.plan.calibrationExamples,
  });
  return normalizeJudgeCommandResult(result);
}

export function renderJudgeResult(result: {
  score: number;
  reasoning: string;
  dimensionScores: Record<string, number>;
  authoritativeParseFailed?: boolean;
  judgeFailed?: boolean;
  parseMethod?: JudgeParseMethod;
  source?: string;
}): string {
  const normalized = normalizeJudgeCommandResult(result);
  return JSON.stringify(
    {
      score: normalized.score,
      reasoning: normalized.reasoning,
      dimensionScores: normalized.dimensionScores,
      authoritativeParseFailed: normalized.authoritativeParseFailed,
      judgeFailed: normalized.judgeFailed,
      ...(normalized.parseMethod ? { parseMethod: normalized.parseMethod } : {}),
      ...(result.source ? { source: result.source } : {}),
    },
    null,
    2,
  );
}

function normalizeJudgeCommandResult(result: {
  score: number;
  reasoning: string;
  dimensionScores: Record<string, number>;
  authoritativeParseFailed?: boolean;
  judgeFailed?: boolean;
  parseMethod?: JudgeParseMethod;
}): JudgeCommandResult {
  validateOptionalBoolean(result.authoritativeParseFailed, "authoritativeParseFailed");
  validateOptionalBoolean(result.judgeFailed, "judgeFailed");
  const authoritativeParseFailed =
    result.authoritativeParseFailed === true || result.parseMethod === "none";
  return {
    score: result.score,
    reasoning: result.reasoning,
    dimensionScores: result.dimensionScores,
    authoritativeParseFailed,
    judgeFailed: authoritativeParseFailed || result.judgeFailed === true,
    ...(result.parseMethod ? { parseMethod: result.parseMethod } : {}),
  };
}

function isJsonObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseOptionalBoolean(value: unknown, field: string): boolean | undefined {
  if (value === undefined || typeof value === "boolean") return value;
  throw new Error(`Invalid ${field}: must be a boolean`);
}

function validateOptionalBoolean(value: unknown, field: string): void {
  parseOptionalBoolean(value, field);
}

function parseJudgeParseMethod(value: unknown): JudgeParseMethod | undefined {
  if (value === undefined) return undefined;
  if (typeof value !== "string") {
    throw new Error("Invalid parseMethod: unsupported judge parse method");
  }
  switch (value) {
    case "raw_json":
    case "code_block":
    case "markers":
    case "plaintext":
    case "none":
    case "delegated":
    case "callback":
      return value;
    default:
      throw new Error("Invalid parseMethod: unsupported judge parse method");
  }
}

function parseDimensionScores(value: unknown): Record<string, number> {
  if (value === undefined) return {};
  if (!isJsonObject(value)) {
    throw new Error("Invalid dimensions: must be an object of scores between 0 and 1");
  }
  const dimensionScores: Record<string, number> = {};
  for (const [dimension, score] of Object.entries(value)) {
    if (typeof score !== "number" || !Number.isFinite(score) || score < 0 || score > 1) {
      throw new Error(`Invalid dimension score '${dimension}': must be between 0 and 1`);
    }
    dimensionScores[dimension] = score;
  }
  return dimensionScores;
}
