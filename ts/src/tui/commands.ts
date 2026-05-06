import type { RunManager } from "../server/run-manager.js";
import type { TuiActivitySettings } from "./activity-summary.js";
import {
  resetTuiActivitySettings,
  saveTuiActivitySettings,
} from "./activity-settings-store.js";
import {
  handleTuiLogin,
  handleTuiLogout,
  handleTuiWhoami,
  resolveTuiAuthSelection,
} from "../server/tui-auth.js";
import { getKnownProvider } from "../config/credentials.js";
import type { TuiPendingLoginState } from "./auth-command.js";
import {
  executeTuiInteractiveCommandWorkflow,
  type TuiInteractiveCommandResult,
} from "./command-workflow.js";
import { formatTuiCommandHelp } from "./meta-command.js";

export type PendingLoginState = TuiPendingLoginState;

export type HandleInteractiveTuiCommandResult = TuiInteractiveCommandResult;

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

async function renderTuiRunStatus(manager: RunManager, runId: string): Promise<string[]> {
  const { renderRunStatus } = await import("../cli/run-inspection-command-workflow.js");
  const { run, generations } = await loadTuiRunInspection(manager, runId);
  return renderRunStatus(run, generations, false).split("\n");
}

async function renderTuiRunShow(
  manager: RunManager,
  runId: string,
  best: boolean,
): Promise<string[]> {
  const { renderRunShow } = await import("../cli/run-inspection-command-workflow.js");
  const { run, generations } = await loadTuiRunInspection(manager, runId);
  return renderRunShow(run, generations, { best }).split("\n");
}

async function loadTuiRuntimeSessionTimeline(
  manager: RunManager,
  runId: string,
): Promise<string[]> {
  const { RuntimeSessionEventStore } = await import("../session/runtime-events.js");
  const { runtimeSessionIdForRun } = await import("../session/runtime-session-ids.js");
  const { executeRuntimeSessionsCommandWorkflow } = await import(
    "../cli/runtime-session-command-workflow.js"
  );
  const store = new RuntimeSessionEventStore(manager.getDbPath());
  try {
    return executeRuntimeSessionsCommandWorkflow({
      plan: {
        action: "timeline",
        sessionId: runtimeSessionIdForRun(runId),
        json: false,
      },
      store,
    }).split("\n");
  } finally {
    store.close();
  }
}

export function formatCommandHelp(): string[] {
  return formatTuiCommandHelp();
}

export async function handleInteractiveTuiCommand(args: {
  manager: RunManager;
  configDir: string;
  raw: string;
  pendingLogin: PendingLoginState | null;
  activitySettings?: TuiActivitySettings;
}): Promise<HandleInteractiveTuiCommandResult> {
  const { manager, configDir, pendingLogin } = args;
  return executeTuiInteractiveCommandWorkflow({
    raw: args.raw,
    pendingLogin,
    activitySettings: args.activitySettings,
  }, {
    pendingLogin: {
      login(provider, apiKey, model, baseUrl) {
        return handleTuiLogin(configDir, provider, apiKey, model, baseUrl);
      },
      selectProvider(provider) {
        return applyProviderSelection(manager, configDir, provider);
      },
    },
    activity: {
      reset() {
        return resetTuiActivitySettings(configDir);
      },
      save(settings) {
        saveTuiActivitySettings(configDir, settings);
      },
    },
    operator: manager,
    solve: manager,
    startRun: manager,
    readActiveRunId() {
      return manager.getState().runId;
    },
    runInspection: {
      renderStatus(runId) {
        return renderTuiRunStatus(manager, runId);
      },
      renderShow(runId, best) {
        return renderTuiRunShow(manager, runId, best);
      },
      renderTimeline(runId) {
        return loadTuiRuntimeSessionTimeline(manager, runId);
      },
    },
    chat: manager,
    authStatus: {
      selectProvider(provider) {
        return applyProviderSelection(manager, configDir, provider);
      },
      readWhoami(preferredProvider) {
        return handleTuiWhoami(configDir, preferredProvider);
      },
      getActiveProvider() {
        return manager.getActiveProviderType() ?? undefined;
      },
    },
    authLogout: {
      logout(provider) {
        handleTuiLogout(configDir, provider);
      },
      clearActiveProvider() {
        manager.clearActiveProvider();
      },
      getActiveProvider() {
        return manager.getActiveProviderType() ?? undefined;
      },
      selectProvider(preferredProvider) {
        return applyProviderSelection(manager, configDir, preferredProvider);
      },
      readWhoami(preferredProvider) {
        return handleTuiWhoami(configDir, preferredProvider);
      },
    },
    authLogin: {
      providerRequiresKey(provider) {
        return getKnownProvider(provider)?.requiresKey ?? true;
      },
      login(provider, apiKey, model, baseUrl) {
        return handleTuiLogin(configDir, provider, apiKey, model, baseUrl);
      },
      selectProvider(provider) {
        return applyProviderSelection(manager, configDir, provider);
      },
    },
  });
}
