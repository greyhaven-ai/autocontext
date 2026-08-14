export const TUI_HELP_TEXT = [
  "autoctx tui [--port 8000] [--connect http://host:port] [--headless]",
  "Starts the pi-tui operator UI with a local server, or attaches to an existing autoctx serve endpoint.",
].join("\n");

export interface TuiCommandValues {
  port?: string;
  headless?: boolean;
  connect?: string;
}

export interface PlannedTuiCommand {
  port: number;
  headless: boolean;
  connect?: string;
}

export function planTuiCommand(
  values: TuiCommandValues,
  stdoutIsTTY: boolean,
): PlannedTuiCommand {
  const connect = values.connect?.trim();
  const port = Number(values.port ?? "8000");
  if (!Number.isInteger(port) || port <= 0 || port > 65_535) {
    throw new Error("--port must be an integer from 1 through 65535");
  }
  const headless = !!values.headless || !stdoutIsTTY;
  if (connect && headless) {
    throw new Error("--connect requires an interactive TTY and cannot be combined with --headless");
  }
  return {
    port,
    headless,
    ...(connect ? { connect } : {}),
  };
}

export function buildHeadlessTuiOutput(input: {
  serverUrl: string;
  scenarios: string[];
}): string[] {
  return [
    `autocontext interactive server listening at ${input.serverUrl}`,
    `Scenarios: ${input.scenarios.join(", ")}`,
  ];
}

export function buildInteractiveTuiRequest<TManager>(input: {
  manager: TManager;
  serverUrl: string;
}): {
  manager: TManager;
  serverUrl: string;
} {
  return {
    manager: input.manager,
    serverUrl: input.serverUrl,
  };
}
