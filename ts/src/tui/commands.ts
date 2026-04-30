import type { RunManager } from "../server/run-manager.js";
import {
  handleTuiLogin,
  handleTuiLogout,
  handleTuiWhoami,
  resolveTuiAuthSelection,
} from "../server/tui-auth.js";
import { getKnownProvider } from "../config/credentials.js";

export interface PendingLoginState {
  provider: string;
  model?: string;
  baseUrl?: string;
}

export interface HandleInteractiveTuiCommandResult {
  logLines: string[];
  pendingLogin: PendingLoginState | null;
  shouldExit?: boolean;
}

function formatWhoamiLines(status: ReturnType<typeof handleTuiWhoami>): string[] {
  const lines = [
    `provider: ${status.provider}`,
    `authenticated: ${status.authenticated ? "yes" : "no"}`,
  ];
  if (status.model) {
    lines.push(`model: ${status.model}`);
  }
  if (status.configuredProviders && status.configuredProviders.length > 0) {
    lines.push(
      `configured providers: ${status.configuredProviders.map((entry) => entry.provider).join(", ")}`,
    );
  }
  return lines;
}

function applyProviderSelection(
  manager: RunManager,
  configDir: string,
  preferredProvider?: string,
) {
  const selection = resolveTuiAuthSelection(configDir, preferredProvider);
  if (selection.provider === "none") {
    manager.clearActiveProvider();
    return selection;
  }
  manager.setActiveProvider({
    providerType: selection.provider,
    ...(selection.apiKey ? { apiKey: selection.apiKey } : {}),
    ...(selection.model ? { model: selection.model } : {}),
    ...(selection.baseUrl ? { baseUrl: selection.baseUrl } : {}),
  });
  return selection;
}

function unquotePlainGoal(raw: string): string {
  const trimmed = raw.trim();
  const quoted = trimmed.match(/^"(.+)"$/);
  return (quoted?.[1] ?? trimmed).trim();
}

function resolveTuiRunId(raw: string, manager: RunManager): string | null {
  const [, runId] = raw.split(/\s+/, 2);
  return runId?.trim() || manager.getState().runId || null;
}

async function loadTuiRunInspection(
  manager: RunManager,
  runId: string,
): Promise<{
  run: import("../cli/run-inspection-command-workflow.js").RunInspectionRun;
  generations: import("../cli/run-inspection-command-workflow.js").RunInspectionGeneration[];
}> {
  const { SQLiteStore } = await import("../storage/index.js");
  const store = new SQLiteStore(manager.getDbPath());
  store.migrate(manager.getMigrationsDir());
  try {
    const run = store.getRun(runId);
    if (!run) {
      throw new Error(`run '${runId}' not found`);
    }
    return {
      run,
      generations: store.getGenerations(runId),
    };
  } finally {
    store.close();
  }
}

export function formatCommandHelp(): string[] {
  return [
    '/solve "plain-language goal"',
    "/run <scenario> [iterations]",
    "/status <run-id>",
    "/show <run-id> --best",
    "/watch <run-id>",
    "/pause or /resume",
    "/hint <text>",
    "/gate <advance|retry|rollback>",
    "/chat <role> <message>",
    "/login <provider> [apiKey]",
    "/logout [provider]",
    "/provider <name>",
    "/whoami",
    "/scenarios",
    "/quit",
  ];
}

export async function handleInteractiveTuiCommand(args: {
  manager: RunManager;
  configDir: string;
  raw: string;
  pendingLogin: PendingLoginState | null;
}): Promise<HandleInteractiveTuiCommandResult> {
  const { manager, configDir, pendingLogin } = args;
  const value = args.raw.trim();

  if (!value) {
    return { logLines: [], pendingLogin };
  }

  if (pendingLogin && !value.startsWith("/")) {
    const loginResult = await handleTuiLogin(
      configDir,
      pendingLogin.provider,
      value,
      pendingLogin.model,
      pendingLogin.baseUrl,
    );
    if (!loginResult.saved) {
      return {
        logLines: [loginResult.validationWarning ?? `Unable to log in to ${pendingLogin.provider}`],
        pendingLogin,
      };
    }
    const status = applyProviderSelection(manager, configDir, loginResult.provider);
    const logLines = [`logged in to ${status.provider}`];
    if (loginResult.validationWarning) {
      logLines.push(`warning: ${loginResult.validationWarning}`);
    }
    return { logLines, pendingLogin: null };
  }

  if (value === "/cancel" && pendingLogin) {
    return { logLines: ["cancelled login prompt"], pendingLogin: null };
  }

  if (value === "/quit" || value === "/exit") {
    return { logLines: [], pendingLogin: null, shouldExit: true };
  }

  if (value === "/help") {
    return { logLines: formatCommandHelp(), pendingLogin: null };
  }

  if (value === "/pause") {
    manager.pause();
    return { logLines: ["paused active loop"], pendingLogin: null };
  }

  if (value === "/resume") {
    manager.resume();
    return { logLines: ["resumed active loop"], pendingLogin: null };
  }

  if (value === "/scenarios") {
    return {
      logLines: [`scenarios: ${manager.listScenarios().join(", ")}`],
      pendingLogin: null,
    };
  }

  if (value.startsWith("/solve ")) {
    const description = unquotePlainGoal(value.slice("/solve ".length));
    if (!description) {
      return { logLines: ['usage: /solve "plain-language goal"'], pendingLogin: null };
    }
    try {
      const preview = await manager.createScenario(description);
      const ready = await manager.confirmScenario();
      const runId = await manager.startRun(ready.name, 5);
      return {
        logLines: [
          `created scenario ${preview.name}`,
          `accepted run ${runId}`,
        ],
        pendingLogin: null,
      };
    } catch (err) {
      return { logLines: [err instanceof Error ? err.message : String(err)], pendingLogin: null };
    }
  }

  if (value.startsWith("/run ")) {
    const [, scenario = "grid_ctf", gensText = "5"] = value.split(/\s+/, 3);
    const generations = Number.parseInt(gensText, 10);
    try {
      const runId = await manager.startRun(scenario, Number.isFinite(generations) ? generations : 5);
      return { logLines: [`accepted run ${runId}`], pendingLogin: null };
    } catch (err) {
      return { logLines: [err instanceof Error ? err.message : String(err)], pendingLogin: null };
    }
  }

  if (value === "/status" || value.startsWith("/status ")) {
    const runId = resolveTuiRunId(value, manager);
    if (!runId) {
      return { logLines: ["usage: /status <run-id>"], pendingLogin: null };
    }
    try {
      const { renderRunStatus } = await import("../cli/run-inspection-command-workflow.js");
      const { run, generations } = await loadTuiRunInspection(manager, runId);
      return { logLines: renderRunStatus(run, generations, false).split("\n"), pendingLogin: null };
    } catch (err) {
      return { logLines: [err instanceof Error ? err.message : String(err)], pendingLogin: null };
    }
  }

  if (value === "/show" || value.startsWith("/show ")) {
    const runId = resolveTuiRunId(value, manager);
    if (!runId) {
      return { logLines: ["usage: /show <run-id> [--best]"], pendingLogin: null };
    }
    try {
      const { renderRunShow } = await import("../cli/run-inspection-command-workflow.js");
      const { run, generations } = await loadTuiRunInspection(manager, runId);
      return {
        logLines: renderRunShow(run, generations, { best: value.includes("--best") }).split("\n"),
        pendingLogin: null,
      };
    } catch (err) {
      return { logLines: [err instanceof Error ? err.message : String(err)], pendingLogin: null };
    }
  }

  if (value === "/watch" || value.startsWith("/watch ")) {
    const runId = resolveTuiRunId(value, manager);
    if (!runId) {
      return { logLines: ["usage: /watch <run-id>"], pendingLogin: null };
    }
    try {
      const { renderRunStatus } = await import("../cli/run-inspection-command-workflow.js");
      const { run, generations } = await loadTuiRunInspection(manager, runId);
      return {
        logLines: [
          `watching ${runId}`,
          ...renderRunStatus(run, generations, false).split("\n"),
        ],
        pendingLogin: null,
      };
    } catch (err) {
      return { logLines: [err instanceof Error ? err.message : String(err)], pendingLogin: null };
    }
  }

  if (value.startsWith("/hint ")) {
    manager.injectHint(value.slice("/hint ".length).trim());
    return { logLines: ["operator hint queued"], pendingLogin: null };
  }

  if (value.startsWith("/gate ")) {
    const decision = value.slice("/gate ".length).trim();
    if (decision === "advance" || decision === "retry" || decision === "rollback") {
      manager.overrideGate(decision);
      return { logLines: [`gate override queued: ${decision}`], pendingLogin: null };
    }
    return { logLines: ["gate override must be advance|retry|rollback"], pendingLogin: null };
  }

  if (value.startsWith("/chat ")) {
    const [, role = "analyst", ...rest] = value.split(/\s+/);
    const message = rest.join(" ").trim();
    if (!message) {
      return { logLines: ["chat command requires a role and message"], pendingLogin: null };
    }
    try {
      const response = await manager.chatAgent(role, message);
      const firstLine = response.split("\n")[0] ?? response;
      return { logLines: [`[${role}] ${firstLine}`], pendingLogin: null };
    } catch (err) {
      return { logLines: [err instanceof Error ? err.message : String(err)], pendingLogin: null };
    }
  }

  if (value.startsWith("/login")) {
    const [, providerRaw, apiKey, model, baseUrl] = value.split(/\s+/, 5);
    const provider = providerRaw?.trim().toLowerCase();
    if (!provider) {
      return {
        logLines: ["usage: /login <provider> [apiKey] [model] [baseUrl]"],
        pendingLogin: null,
      };
    }

    const providerInfo = getKnownProvider(provider);
    const requiresKey = providerInfo?.requiresKey ?? true;
    if (!apiKey && requiresKey) {
      return {
        logLines: [`enter API key for ${provider} on the next line, or /cancel`],
        pendingLogin: { provider, ...(model ? { model } : {}), ...(baseUrl ? { baseUrl } : {}) },
      };
    }

    const loginResult = await handleTuiLogin(configDir, provider, apiKey, model, baseUrl);
    if (!loginResult.saved) {
      return {
        logLines: [loginResult.validationWarning ?? `Unable to log in to ${provider}`],
        pendingLogin: null,
      };
    }
    const status = applyProviderSelection(manager, configDir, loginResult.provider);
    const logLines = [`logged in to ${status.provider}`];
    if (loginResult.validationWarning) {
      logLines.push(`warning: ${loginResult.validationWarning}`);
    }
    return { logLines, pendingLogin: null };
  }

  if (value.startsWith("/logout")) {
    const [, providerRaw] = value.split(/\s+/, 2);
    const provider = providerRaw?.trim().toLowerCase();
    handleTuiLogout(configDir, provider);
    if (!provider) {
      manager.clearActiveProvider();
      const status = handleTuiWhoami(configDir);
      return {
        logLines: ["cleared stored credentials", ...formatWhoamiLines(status)],
        pendingLogin: null,
      };
    }
    const status = applyProviderSelection(
      manager,
      configDir,
      manager.getActiveProviderType() === provider ? provider : manager.getActiveProviderType() ?? undefined,
    );
    return {
      logLines: [`logged out of ${provider}`, ...formatWhoamiLines(status)],
      pendingLogin: null,
    };
  }

  if (value.startsWith("/provider ")) {
    const [, providerRaw] = value.split(/\s+/, 2);
    const provider = providerRaw?.trim().toLowerCase();
    if (!provider) {
      return { logLines: ["usage: /provider <name>"], pendingLogin: null };
    }
    const status = applyProviderSelection(manager, configDir, provider);
    return {
      logLines: [
        `active provider: ${status.provider}`,
        ...formatWhoamiLines(handleTuiWhoami(configDir, status.provider)),
      ],
      pendingLogin: null,
    };
  }

  if (value === "/whoami") {
    const status = handleTuiWhoami(configDir, manager.getActiveProviderType() ?? undefined);
    return { logLines: formatWhoamiLines(status), pendingLogin: null };
  }

  return { logLines: ["unknown command; use /help"], pendingLogin: null };
}
