export type TuiRunCommandTarget =
  | {
      readonly kind: "target";
      readonly runId: string;
    }
  | {
      readonly kind: "missing";
    };

type TuiRunInspectionCommandName = "status" | "show" | "watch" | "timeline";
type TuiActiveRunIdSource = string | null | undefined | (() => string | null | undefined);

const TUI_RUN_INSPECTION_USAGES: Record<TuiRunInspectionCommandName, string> = {
  status: "/status <run-id>",
  show: "/show <run-id> [--best]",
  watch: "/watch <run-id>",
  timeline: "/timeline <run-id>",
};

export type TuiRunInspectionCommandPlan =
  | {
      readonly kind: "unhandled";
    }
  | {
      readonly kind: "usage";
      readonly usageLine: string;
    }
  | {
      readonly kind: "status";
      readonly runId: string;
    }
  | {
      readonly kind: "show";
      readonly runId: string;
      readonly best: boolean;
    }
  | {
      readonly kind: "watch";
      readonly runId: string;
    }
  | {
      readonly kind: "timeline";
      readonly runId: string;
    };

export interface TuiRunInspectionCommandEffects {
  renderStatus(runId: string): Promise<string[]>;
  renderShow(runId: string, best: boolean): Promise<string[]>;
  renderTimeline(runId: string): Promise<string[]>;
}

export interface TuiRunInspectionCommandExecutionResult {
  logLines: string[];
}

export type TuiStartRunCommandPlan =
  | {
      readonly kind: "unhandled";
    }
  | {
      readonly kind: "start";
      readonly scenario: string;
      readonly iterations: number;
    };

export interface TuiStartRunCommandEffects {
  startRun(scenario: string, iterations: number): Promise<string>;
}

export interface TuiStartRunCommandExecutionResult {
  logLines: string[];
}

export function planTuiStartRunCommand(raw: string): TuiStartRunCommandPlan {
  const value = raw.trim();
  if (!value.startsWith("/run ")) {
    return {
      kind: "unhandled",
    };
  }

  const [, scenario = "grid_ctf", iterationsText = "5"] = value.split(/\s+/, 3);
  const iterations = Number.parseInt(iterationsText, 10);
  return {
    kind: "start",
    scenario,
    iterations: Number.isFinite(iterations) ? iterations : 5,
  };
}

export async function executeTuiStartRunCommandPlan(
  plan: TuiStartRunCommandPlan,
  effects: TuiStartRunCommandEffects,
): Promise<TuiStartRunCommandExecutionResult | null> {
  if (plan.kind === "unhandled") {
    return null;
  }
  try {
    const runId = await effects.startRun(plan.scenario, plan.iterations);
    return { logLines: [`accepted run ${runId}`] };
  } catch (err) {
    return { logLines: [err instanceof Error ? err.message : String(err)] };
  }
}

export function resolveTuiRunCommandTarget(
  raw: string,
  activeRunId?: string | null,
): TuiRunCommandTarget {
  const [, explicitRunId] = raw.trim().split(/\s+/, 2);
  const runId = explicitRunId?.trim() || activeRunId?.trim();
  if (!runId) {
    return {
      kind: "missing",
    };
  }
  return {
    kind: "target",
    runId,
  };
}

export function planTuiRunInspectionCommand(
  raw: string,
  activeRunId?: TuiActiveRunIdSource,
): TuiRunInspectionCommandPlan {
  const command = readTuiRunInspectionCommandName(raw);
  if (!command) {
    return {
      kind: "unhandled",
    };
  }

  const target = resolveTuiRunCommandTarget(raw, readTuiActiveRunId(activeRunId));
  if (target.kind === "missing") {
    return {
      kind: "usage",
      usageLine: `usage: ${TUI_RUN_INSPECTION_USAGES[command]}`,
    };
  }

  if (command === "show") {
    return {
      kind: "show",
      runId: target.runId,
      best: raw.includes("--best"),
    };
  }
  return {
    kind: command,
    runId: target.runId,
  };
}

export async function executeTuiRunInspectionCommandPlan(
  plan: TuiRunInspectionCommandPlan,
  effects: TuiRunInspectionCommandEffects,
): Promise<TuiRunInspectionCommandExecutionResult | null> {
  if (plan.kind === "unhandled") {
    return null;
  }
  if (plan.kind === "usage") {
    return { logLines: [plan.usageLine] };
  }

  try {
    switch (plan.kind) {
      case "status":
        return { logLines: await effects.renderStatus(plan.runId) };
      case "show":
        return { logLines: await effects.renderShow(plan.runId, plan.best) };
      case "watch":
        return {
          logLines: [`watching ${plan.runId}`, ...(await effects.renderStatus(plan.runId))],
        };
      case "timeline":
        return { logLines: await effects.renderTimeline(plan.runId) };
    }
  } catch (err) {
    return { logLines: [err instanceof Error ? err.message : String(err)] };
  }
}

function readTuiRunInspectionCommandName(raw: string): TuiRunInspectionCommandName | null {
  const match = raw.trim().match(/^\/(status|show|watch|timeline)(?:\s|$)/);
  return match ? (match[1] as TuiRunInspectionCommandName) : null;
}

function readTuiActiveRunId(source: TuiActiveRunIdSource): string | null | undefined {
  return typeof source === "function" ? source() : source;
}
