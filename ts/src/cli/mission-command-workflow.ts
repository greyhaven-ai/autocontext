export const MISSION_HELP_TEXT = `autoctx mission — Manage verifier-driven missions

Subcommands:
  create     Create a new mission
  run        Advance a mission and write a checkpoint
  status     Show mission details
  list       List all missions
  pause      Pause an active mission
  resume     Resume a paused mission
  cancel     Cancel a mission
  artifacts  Inspect saved mission checkpoints

Examples:
  autoctx mission create --name "Ship login" --goal "Implement OAuth"
  autoctx mission create --type code --name "Fix login" --goal "Tests pass" --repo-path . --test-command "npm test"
  autoctx mission run --id mission-abc123 --max-iterations 3
  autoctx mission list --status active
  autoctx mission status --id mission-abc123
  autoctx mission artifacts --id mission-abc123

See also: run, improve, judge`;

export interface MissionCreateValues {
  type?: string;
  name?: string;
  goal?: string;
  "max-steps"?: string;
  "repo-path"?: string;
  "test-command"?: string;
  "lint-command"?: string;
  "build-command"?: string;
}

export interface MissionRunValues {
  id?: string;
  "max-iterations"?: string;
  "step-description"?: string;
}

function parseOptionalPositiveInteger(raw: string | undefined, label: string): number | undefined {
  if (!raw) {
    return undefined;
  }
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`${label} must be a positive integer`);
  }
  return parsed;
}

function parsePositiveIntegerWithDefault(
  raw: string | undefined,
  fallback: number,
  label: string,
): number {
  return parseOptionalPositiveInteger(raw, label) ?? fallback;
}

export function getMissionIdOrThrow(
  values: { id?: string },
  usage: string,
): string {
  if (!values.id) {
    throw new Error(usage);
  }
  return values.id;
}

export function planMissionCreate(
  values: MissionCreateValues,
  resolvePath: (value: string) => string,
):
  | {
      missionType: "generic";
      name: string;
      goal: string;
      budget?: { maxSteps: number };
    }
  | {
      missionType: "code";
      name: string;
      goal: string;
      budget?: { maxSteps: number };
      repoPath: string;
      testCommand: string;
      lintCommand?: string;
      buildCommand?: string;
    } {
  if (!values.name || !values.goal) {
    throw new Error(
      "Usage: autoctx mission create --name <name> --goal <goal> [--type code --repo-path <path> --test-command <cmd> [--lint-command <cmd>] [--build-command <cmd>]] [--max-steps N]",
    );
  }

  const budgetMaxSteps = parseOptionalPositiveInteger(values["max-steps"], "--max-steps");
  const budget = budgetMaxSteps ? { maxSteps: budgetMaxSteps } : undefined;
  const missionType =
    values.type === "code" ||
    values["repo-path"] ||
    values["test-command"] ||
    values["lint-command"] ||
    values["build-command"]
      ? "code"
      : "generic";

  if (missionType === "code") {
    if (!values["repo-path"] || !values["test-command"]) {
      throw new Error("Code missions require --repo-path and --test-command.");
    }
    return {
      missionType,
      name: values.name,
      goal: values.goal,
      ...(budget ? { budget } : {}),
      repoPath: resolvePath(values["repo-path"]),
      testCommand: values["test-command"],
      ...(values["lint-command"] ? { lintCommand: values["lint-command"] } : {}),
      ...(values["build-command"] ? { buildCommand: values["build-command"] } : {}),
    };
  }

  return {
    missionType,
    name: values.name,
    goal: values.goal,
    ...(budget ? { budget } : {}),
  };
}

export function planMissionRun(
  values: MissionRunValues,
  mission: { metadata?: Record<string, unknown> },
): {
  id: string;
  maxIterations: number;
  stepDescription?: string;
  needsAdaptivePlanning: boolean;
} {
  const id = getMissionIdOrThrow(
    values,
    "Usage: autoctx mission run --id <mission-id> [--max-iterations N] [--step-description <text>]",
  );
  const missionType = mission.metadata?.missionType;
  return {
    id,
    maxIterations: parsePositiveIntegerWithDefault(
      values["max-iterations"],
      1,
      "--max-iterations",
    ),
    ...(values["step-description"] ? { stepDescription: values["step-description"] } : {}),
    needsAdaptivePlanning: missionType !== "code" && missionType !== "proof",
  };
}

export function planMissionList(values: { status?: string }): {
  status?: string;
} {
  return { status: values.status };
}

export function buildMissionCheckpointPayload<TPayload extends Record<string, unknown>>(
  payload: TPayload,
  checkpointPath: string,
): TPayload & { checkpointPath: string } {
  return {
    ...payload,
    checkpointPath,
  };
}
