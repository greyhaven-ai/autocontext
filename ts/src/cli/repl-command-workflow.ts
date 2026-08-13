export const REPL_HELP_TEXT = `autoctx repl — run a direct iterative task session

Usage:
  autoctx repl (--scenario <saved-scenario> | --prompt <task-prompt> --rubric <rubric>) [options]

Options:
  -s, --scenario <name>         Saved task scenario
  -p, --prompt <text>           Task prompt
  -r, --rubric <text>           Evaluation rubric
  -o, --output <text>           Current output for revise mode
  --phase <generate|revise>     Session phase (default: generate)
  --reference-context <text>    Additional reference material
  --required-concept <text>     Required concept; repeatable
  -m, --model <name>            Model override
  -n, --turns <N>               Maximum turns (default: 6)
  --max-tokens <N>              Maximum tokens per turn (default: 2048)
  -t, --temperature <N>         Sampling temperature (default: 0.2)
  --max-stdout <N>              Maximum stdout characters (default: 8192)
  --timeout-ms <N>              Code timeout in milliseconds (default: 10000)
  --memory-mb <N>               Code memory limit in MiB (default: 64)`;

export interface ReplCommandValues {
  scenario?: string;
  prompt?: string;
  rubric?: string;
  output?: string;
  phase?: string;
  "reference-context"?: string;
  "required-concept"?: string[];
  model?: string;
  turns?: string;
  "max-tokens"?: string;
  temperature?: string;
  "max-stdout"?: string;
  "timeout-ms"?: string;
  "memory-mb"?: string;
}

interface SavedReplScenario {
  taskPrompt?: string;
  rubric?: string;
  referenceContext?: string;
  requiredConcepts?: string[];
}

export interface PlannedReplCommand {
  phase: "generate" | "revise";
  taskPrompt: string;
  rubric: string;
  currentOutput?: string;
  referenceContext?: string;
  requiredConcepts: string[];
  config: {
    enabled: true;
    model?: string;
    maxTurns: number;
    maxTokensPerTurn: number;
    temperature: number;
    maxStdoutChars: number;
    codeTimeoutMs: number;
    memoryLimitMb: number;
  };
}

export function getReplUsageExitCode(help: boolean): number {
  return help ? 0 : 1;
}

export function parseReplPhase(phase?: string): "generate" | "revise" {
  return phase === "revise" ? "revise" : "generate";
}

function mergeUniqueStrings(
  left?: string[],
  right?: string[],
): string[] {
  return [...new Set([...(left ?? []), ...(right ?? [])])];
}

export function planReplCommand(
  values: ReplCommandValues,
  savedScenario: SavedReplScenario | null,
): PlannedReplCommand {
  const phase = parseReplPhase(values.phase);
  if (phase === "revise" && !values.output) {
    throw new Error("autoctx repl --phase revise requires -o/--output");
  }

  const taskPrompt = values.prompt ?? savedScenario?.taskPrompt;
  const rubric = values.rubric ?? savedScenario?.rubric;
  if (!taskPrompt || !rubric) {
    throw new Error(
      "Error: repl requires either --scenario <name> or both --prompt and --rubric.",
    );
  }

  return {
    phase,
    taskPrompt,
    rubric,
    currentOutput: values.output,
    referenceContext:
      values["reference-context"] ?? savedScenario?.referenceContext,
    requiredConcepts: mergeUniqueStrings(
      savedScenario?.requiredConcepts,
      values["required-concept"],
    ),
    config: {
      enabled: true,
      model: values.model,
      maxTurns: Number.parseInt(values.turns ?? "6", 10),
      maxTokensPerTurn: Number.parseInt(values["max-tokens"] ?? "2048", 10),
      temperature: Number.parseFloat(values.temperature ?? "0.2"),
      maxStdoutChars: Number.parseInt(values["max-stdout"] ?? "8192", 10),
      codeTimeoutMs: Number.parseInt(values["timeout-ms"] ?? "10000", 10),
      memoryLimitMb: Number.parseInt(values["memory-mb"] ?? "64", 10),
    },
  };
}

export function buildReplSessionRequest<TProvider>(input: {
  provider: TProvider;
  model: string;
  plan: PlannedReplCommand;
}): {
  provider: TProvider;
  model: string;
  config: PlannedReplCommand["config"];
  phase: PlannedReplCommand["phase"];
  taskPrompt: string;
  rubric: string;
  currentOutput?: string;
  referenceContext?: string;
  requiredConcepts: string[];
} {
  return {
    provider: input.provider,
    model: input.model,
    config: input.plan.config,
    phase: input.plan.phase,
    taskPrompt: input.plan.taskPrompt,
    rubric: input.plan.rubric,
    currentOutput: input.plan.currentOutput,
    referenceContext: input.plan.referenceContext,
    requiredConcepts: input.plan.requiredConcepts,
  };
}
