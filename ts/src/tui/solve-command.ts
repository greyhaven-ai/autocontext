export const TUI_SOLVE_USAGE = 'usage: /solve "plain-language goal"';

export type TuiSolveCommandPlan =
  | {
      readonly kind: "unhandled";
    }
  | {
      readonly kind: "usage";
      readonly usageLine: string;
    }
  | {
      readonly kind: "solve";
      readonly description: string;
      readonly iterations: 5;
    };

export interface TuiSolveCommandScenario {
  readonly name: string;
}

export interface TuiSolveCommandEffects {
  createScenario(description: string): Promise<TuiSolveCommandScenario>;
  confirmScenario(): Promise<TuiSolveCommandScenario>;
  startRun(scenario: string, iterations: number): Promise<string>;
}

export interface TuiSolveCommandExecutionResult {
  logLines: string[];
}

export function planTuiSolveCommand(raw: string): TuiSolveCommandPlan {
  const value = raw.trim();
  if (!value.startsWith("/solve ")) {
    return {
      kind: "unhandled",
    };
  }

  const description = unquotePlainGoal(value.slice("/solve ".length));
  if (!description) {
    return {
      kind: "usage",
      usageLine: TUI_SOLVE_USAGE,
    };
  }

  return {
    kind: "solve",
    description,
    iterations: 5,
  };
}

export async function executeTuiSolveCommandPlan(
  plan: TuiSolveCommandPlan,
  effects: TuiSolveCommandEffects,
): Promise<TuiSolveCommandExecutionResult | null> {
  switch (plan.kind) {
    case "unhandled":
      return null;
    case "usage":
      return { logLines: [plan.usageLine] };
    case "solve":
      try {
        const preview = await effects.createScenario(plan.description);
        const ready = await effects.confirmScenario();
        const runId = await effects.startRun(ready.name, plan.iterations);
        return {
          logLines: [
            `created scenario ${preview.name}`,
            `accepted run ${runId}`,
          ],
        };
      } catch (err) {
        return { logLines: [err instanceof Error ? err.message : String(err)] };
      }
  }
}

function unquotePlainGoal(raw: string): string {
  const trimmed = raw.trim();
  const quoted = trimmed.match(/^"(.+)"$/);
  return (quoted?.[1] ?? trimmed).trim();
}
