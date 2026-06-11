import {
  buildLifecycleSessionEvent,
  type NormalizedSessionEvent,
} from "./background-session-events.js";

export type LifecycleHookName = "setup" | "start";
export type LifecycleHookFailurePolicy = "continue" | "fail_session";
export type LifecycleHookPhase = "skipped" | "started" | "completed" | "failed" | "timeout";

export interface LifecycleHookContext {
  readonly session_id: string;
  readonly run_id?: string;
  readonly task_id?: string;
  readonly worker_id?: string;
}

export interface LifecycleHookDefinition {
  readonly command?: readonly string[];
  readonly cwd?: string;
  readonly env?: Readonly<Record<string, string>>;
  readonly timeout_ms?: number;
  readonly failure_policy?: LifecycleHookFailurePolicy;
}

export interface LifecycleHookInvocation {
  readonly hook: LifecycleHookName;
  readonly command: readonly string[];
  readonly cwd?: string;
  readonly env: Readonly<Record<string, string>>;
  readonly timeout_ms?: number;
  readonly context: LifecycleHookContext;
}

export interface LifecycleHookRunnerResult {
  readonly exit_code?: number;
  readonly stdout?: string;
  readonly stderr?: string;
  readonly error?: string;
  readonly timed_out?: boolean;
}

export type LifecycleHookRunner = (
  invocation: LifecycleHookInvocation,
) => Promise<LifecycleHookRunnerResult> | LifecycleHookRunnerResult;

export interface LifecycleHookOutcome {
  readonly hook: LifecycleHookName;
  readonly phase: Exclude<LifecycleHookPhase, "started">;
  readonly ok: boolean;
  readonly terminal: boolean;
  readonly failure_policy: LifecycleHookFailurePolicy;
  readonly exit_code?: number;
  readonly error?: string;
  readonly timed_out?: boolean;
}

export interface LifecycleHookExecutionResult {
  readonly outcome: LifecycleHookOutcome;
  readonly events: readonly NormalizedSessionEvent[];
  readonly next_sequence: number;
}

export interface ExecuteLifecycleHookOptions {
  readonly hook: LifecycleHookName;
  readonly definition?: LifecycleHookDefinition | null;
  readonly context: LifecycleHookContext;
  readonly sequence: number;
  readonly timestamp?: string;
  readonly runner: LifecycleHookRunner;
}

export interface ExecuteBackgroundSessionLifecycleHooksOptions {
  readonly hooks: Partial<Record<LifecycleHookName, LifecycleHookDefinition | null>>;
  readonly context: LifecycleHookContext;
  readonly sequence: number;
  readonly timestamp?: string;
  readonly runner: LifecycleHookRunner;
}

export interface BackgroundSessionLifecycleHooksResult {
  readonly outcomes: readonly LifecycleHookOutcome[];
  readonly events: readonly NormalizedSessionEvent[];
  readonly terminal: boolean;
  readonly next_sequence: number;
}

const LIFECYCLE_HOOK_ORDER: readonly LifecycleHookName[] = ["setup", "start"];

export function buildLifecycleHookEnv(
  context: LifecycleHookContext,
  hook: LifecycleHookName,
  extraEnv: Readonly<Record<string, string>> = {},
): Record<string, string> {
  const env: Record<string, string> = {
    AUTOCTX_BACKGROUND_SESSION_ID: context.session_id,
    AUTOCTX_SESSION_ID: context.session_id,
    AUTOCTX_HOOK_NAME: hook,
  };
  addIfPresent(env, "AUTOCTX_RUN_ID", context.run_id);
  addIfPresent(env, "AUTOCTX_TASK_ID", context.task_id);
  addIfPresent(env, "AUTOCTX_WORKER_ID", context.worker_id);
  return { ...env, ...extraEnv };
}

export async function executeBackgroundSessionLifecycleHooks(
  options: ExecuteBackgroundSessionLifecycleHooksOptions,
): Promise<BackgroundSessionLifecycleHooksResult> {
  const outcomes: LifecycleHookOutcome[] = [];
  const events: NormalizedSessionEvent[] = [];
  let nextSequence = options.sequence;
  let terminal = false;

  for (const hook of LIFECYCLE_HOOK_ORDER) {
    if (!(hook in options.hooks)) {
      continue;
    }
    const result = await executeLifecycleHook({
      hook,
      definition: options.hooks[hook],
      context: options.context,
      sequence: nextSequence,
      timestamp: options.timestamp,
      runner: options.runner,
    });
    outcomes.push(result.outcome);
    events.push(...result.events);
    nextSequence = result.next_sequence;
    terminal = result.outcome.terminal;
    if (terminal) {
      break;
    }
  }

  return { outcomes, events, terminal, next_sequence: nextSequence };
}

export async function executeLifecycleHook(
  options: ExecuteLifecycleHookOptions,
): Promise<LifecycleHookExecutionResult> {
  assertLifecycleHookName(options.hook);
  const definition = options.definition ?? null;
  const failurePolicy = definition?.failure_policy ?? defaultFailurePolicy(options.hook);
  const timestamp = options.timestamp ?? new Date().toISOString();
  const command = normalizeCommand(definition?.command);

  if (command.length === 0) {
    const event = buildLifecycleSessionEvent({
      sessionId: options.context.session_id,
      sequence: options.sequence,
      timestamp,
      hook: options.hook,
      phase: "skipped",
    });
    return {
      outcome: {
        hook: options.hook,
        phase: "skipped",
        ok: true,
        terminal: false,
        failure_policy: failurePolicy,
      },
      events: [event],
      next_sequence: options.sequence + 1,
    };
  }

  const started = buildLifecycleSessionEvent({
    sessionId: options.context.session_id,
    sequence: options.sequence,
    timestamp,
    hook: options.hook,
    phase: "started",
  });
  const invocation: LifecycleHookInvocation = {
    hook: options.hook,
    command,
    ...(definition?.cwd ? { cwd: definition.cwd } : {}),
    env: buildLifecycleHookEnv(options.context, options.hook, definition?.env),
    ...(typeof definition?.timeout_ms === "number" ? { timeout_ms: definition.timeout_ms } : {}),
    context: options.context,
  };

  let runnerResult: LifecycleHookRunnerResult;
  try {
    runnerResult = await options.runner(invocation);
  } catch (error) {
    runnerResult = { error: error instanceof Error ? error.message : String(error) };
  }

  const phase = phaseForRunnerResult(runnerResult);
  const ok = phase === "completed";
  const terminal = !ok && failurePolicy === "fail_session";
  const finished = buildLifecycleSessionEvent({
    sessionId: options.context.session_id,
    sequence: options.sequence + 1,
    timestamp,
    hook: options.hook,
    phase,
  });
  const outcome: LifecycleHookOutcome = {
    hook: options.hook,
    phase,
    ok,
    terminal,
    failure_policy: failurePolicy,
    ...optionalNumber("exit_code", runnerResult.exit_code),
    ...optionalString("error", errorForRunnerResult(runnerResult, phase)),
    ...(runnerResult.timed_out === true ? { timed_out: true } : {}),
  };

  return { outcome, events: [started, finished], next_sequence: options.sequence + 2 };
}

function phaseForRunnerResult(
  result: LifecycleHookRunnerResult,
): Exclude<LifecycleHookPhase, "started" | "skipped"> {
  if (result.timed_out === true) {
    return "timeout";
  }
  if (typeof result.exit_code === "number" && result.exit_code !== 0) {
    return "failed";
  }
  if (result.error && result.error.trim()) {
    return "failed";
  }
  return "completed";
}

function errorForRunnerResult(
  result: LifecycleHookRunnerResult,
  phase: LifecycleHookOutcome["phase"],
): string {
  if (phase === "completed" || phase === "skipped") {
    return "";
  }
  if (result.error && result.error.trim()) {
    return result.error;
  }
  if (result.stderr && result.stderr.trim()) {
    return result.stderr;
  }
  if (phase === "timeout") {
    return "Lifecycle hook timed out";
  }
  return typeof result.exit_code === "number"
    ? `Lifecycle hook exited with code ${result.exit_code}`
    : "Lifecycle hook failed";
}

function defaultFailurePolicy(hook: LifecycleHookName): LifecycleHookFailurePolicy {
  return hook === "start" ? "fail_session" : "continue";
}

function normalizeCommand(command: readonly string[] | undefined): readonly string[] {
  if (!command) {
    return [];
  }
  return command.filter((part) => part.trim().length > 0);
}

function addIfPresent(
  target: Record<string, string>,
  key: string,
  value: string | undefined,
): void {
  if (value && value.trim()) {
    target[key] = value;
  }
}

function optionalNumber(
  key: "exit_code",
  value: number | undefined,
): Partial<Pick<LifecycleHookOutcome, "exit_code">> {
  return typeof value === "number" ? { [key]: value } : {};
}

function optionalString(key: "error", value: string): Partial<Pick<LifecycleHookOutcome, "error">> {
  return value ? { [key]: value } : {};
}

function assertLifecycleHookName(hook: string): asserts hook is LifecycleHookName {
  if (hook !== "setup" && hook !== "start") {
    throw new RangeError(`Unsupported lifecycle hook: ${hook}`);
  }
}
