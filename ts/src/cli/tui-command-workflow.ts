export const TUI_HELP_TEXT = [
  "autoctx tui [--port 8000] [--headless]",
  "Starts the interactive WebSocket server and bundled terminal UI.",
].join("\n");

export interface TuiCommandValues {
  port?: string;
  headless?: boolean;
}

export interface PlannedTuiCommand {
  port: number;
  headless: boolean;
}

export function planTuiCommand(
  values: TuiCommandValues,
  stdoutIsTTY: boolean,
): PlannedTuiCommand {
  return {
    port: Number.parseInt(values.port ?? "8000", 10),
    headless: !!values.headless || !stdoutIsTTY,
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
