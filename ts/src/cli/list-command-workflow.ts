export const LIST_HELP_TEXT = `autoctx list — List recent runs

Usage: autoctx list [options]

Options:
  --limit N            Maximum number of runs to show (default: 50)
  --scenario <name>    Filter runs by scenario name
  --json               Output as JSON array

See also: run, replay, status`;

export interface ListCommandValues {
  limit?: string;
  scenario?: string;
  json?: boolean;
}

export interface ListCommandPlan {
  limit: number;
  scenario?: string;
  json: boolean;
}

export interface ListedRun {
  run_id: string;
  scenario: string;
  status: string;
  created_at: string;
}

export function planListCommand(values: ListCommandValues): ListCommandPlan {
  return {
    limit: Number.parseInt(values.limit ?? "50", 10),
    scenario: values.scenario,
    json: !!values.json,
  };
}

export function renderListRuns(runs: ListedRun[], json: boolean): string {
  if (json) {
    return JSON.stringify(runs, null, 2);
  }
  if (runs.length === 0) {
    return "No runs found.";
  }
  return runs
    .map((run) => `${run.run_id}  ${run.scenario}  ${run.status}  ${run.created_at}`)
    .join("\n");
}

export function executeListCommandWorkflow(opts: {
  plan: ListCommandPlan;
  listRuns: (limit: number, scenario?: string) => ListedRun[];
}): string {
  return renderListRuns(opts.listRuns(opts.plan.limit, opts.plan.scenario), opts.plan.json);
}
