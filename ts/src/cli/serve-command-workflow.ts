export const SERVE_HELP_TEXT = [
  "autoctx serve [--port 8000] [--host 127.0.0.1] [--json]",
  "Starts the HTTP API server (matches Python 'autoctx serve').",
  "With --json, prints a machine-parseable JSON line on startup.",
].join("\n");

export interface ServeCommandValues {
  port?: string;
  host?: string;
  json?: boolean;
}

export interface ServeCommandPlan {
  port: number;
  host: string;
  json: boolean;
}

export interface ServeStartupInfo {
  url: string;
  apiUrl: string;
  wsUrl: string;
  host: string;
  port: number;
  scenarios: string[];
}

export function planServeCommand(values: ServeCommandValues): ServeCommandPlan {
  return {
    port: Number.parseInt(values.port ?? "8000", 10),
    host: values.host ?? "127.0.0.1",
    json: !!values.json,
  };
}

export function renderServeStartup(startupInfo: ServeStartupInfo, json: boolean): string[] {
  if (json) {
    return [JSON.stringify(startupInfo)];
  }
  return [
    `autocontext server listening at ${startupInfo.url}`,
    `API: ${startupInfo.apiUrl}`,
    `WebSocket: ${startupInfo.wsUrl}`,
    `Scenarios: ${startupInfo.scenarios.join(", ")}`,
  ];
}
