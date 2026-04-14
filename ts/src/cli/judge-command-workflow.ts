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

export function parseDelegatedJudgeInput(input: string): {
  score: number;
  reasoning: string;
  dimensionScores: Record<string, number>;
  source: "delegated";
} {
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(input.trim()) as Record<string, unknown>;
  } catch {
    throw new Error("Invalid JSON on stdin");
  }

  const score = parsed.score as number;
  if (typeof score !== "number" || score < 0 || score > 1) {
    throw new Error("Invalid score: must be a number between 0 and 1");
  }

  return {
    score,
    reasoning: typeof parsed.reasoning === "string" ? parsed.reasoning : "",
    dimensionScores: (parsed.dimensions ?? parsed.dimensionScores ?? {}) as Record<string, number>,
    source: "delegated",
  };
}

export function planJudgeCommand(
  values: JudgeCommandValues,
  savedScenario: {
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
} {
  const taskPrompt = values.prompt ?? savedScenario?.taskPrompt;
  const rubric = values.rubric ?? savedScenario?.rubric;
  const agentOutput = values.output;

  if (!taskPrompt || !rubric || !agentOutput) {
    throw new Error(
      "Error: judge requires either --scenario <name> or both --prompt and --rubric.",
    );
  }

  return {
    taskPrompt,
    rubric,
    agentOutput,
    referenceContext: savedScenario?.referenceContext,
    requiredConcepts: savedScenario?.requiredConcepts,
    calibrationExamples: savedScenario?.calibrationExamples,
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
    }>;
  };
}): Promise<{
  score: number;
  reasoning: string;
  dimensionScores: Record<string, number>;
}> {
  const judge = opts.createJudge({
    provider: opts.provider,
    model: opts.model,
    rubric: opts.plan.rubric,
  });

  return judge.evaluate({
    taskPrompt: opts.plan.taskPrompt,
    agentOutput: opts.plan.agentOutput,
    referenceContext: opts.plan.referenceContext,
    requiredConcepts: opts.plan.requiredConcepts,
    calibrationExamples: opts.plan.calibrationExamples,
  });
}

export function renderJudgeResult(result: {
  score: number;
  reasoning: string;
  dimensionScores: Record<string, number>;
  source?: string;
}): string {
  return JSON.stringify(
    {
      score: result.score,
      reasoning: result.reasoning,
      dimensionScores: result.dimensionScores,
      ...(result.source ? { source: result.source } : {}),
    },
    null,
    2,
  );
}
