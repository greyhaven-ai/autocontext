import {
  DEFAULT_TUI_ACTIVITY_SETTINGS,
  type TuiActivitySettings,
} from "./activity-summary.js";
import {
  executeTuiActivityCommandPlan,
  planTuiActivityCommand,
  type TuiActivityCommandEffects,
} from "./activity-command.js";
import {
  executeTuiAuthLoginCommandPlan,
  executeTuiAuthLogoutCommandPlan,
  executeTuiAuthStatusCommandPlan,
  executeTuiPendingLoginSubmission,
  planTuiAuthCommand,
  type TuiAuthLoginCommandEffects,
  type TuiAuthLogoutCommandEffects,
  type TuiAuthStatusCommandEffects,
  type TuiPendingLoginState,
} from "./auth-command.js";
import {
  executeTuiChatCommandPlan,
  planTuiChatCommand,
  type TuiChatCommandEffects,
} from "./chat-command.js";
import {
  formatTuiCommandHelp,
  planTuiMetaCommand,
} from "./meta-command.js";
import {
  executeTuiOperatorCommandPlan,
  planTuiOperatorCommand,
  type TuiOperatorCommandEffects,
} from "./operator-command.js";
import {
  executeTuiRunInspectionCommandPlan,
  executeTuiStartRunCommandPlan,
  planTuiRunInspectionCommand,
  planTuiStartRunCommand,
  type TuiRunInspectionCommandEffects,
  type TuiStartRunCommandEffects,
} from "./run-command.js";
import {
  executeTuiSolveCommandPlan,
  planTuiSolveCommand,
  type TuiSolveCommandEffects,
} from "./solve-command.js";

export interface TuiInteractiveCommandRequest {
  raw: string;
  pendingLogin: TuiPendingLoginState | null;
  activitySettings?: TuiActivitySettings;
}

export interface TuiInteractiveCommandResult {
  logLines: string[];
  pendingLogin: TuiPendingLoginState | null;
  activitySettings?: TuiActivitySettings;
  shouldExit?: boolean;
}

export interface TuiInteractiveCommandEffects {
  pendingLogin: Pick<TuiAuthLoginCommandEffects, "login" | "selectProvider">;
  activity: TuiActivityCommandEffects;
  operator: TuiOperatorCommandEffects;
  solve: TuiSolveCommandEffects;
  startRun: TuiStartRunCommandEffects;
  readActiveRunId(): string | null | undefined;
  runInspection: TuiRunInspectionCommandEffects;
  chat: TuiChatCommandEffects;
  authStatus: TuiAuthStatusCommandEffects;
  authLogout: TuiAuthLogoutCommandEffects;
  authLogin: TuiAuthLoginCommandEffects;
}

export async function executeTuiInteractiveCommandWorkflow(
  request: TuiInteractiveCommandRequest,
  effects: TuiInteractiveCommandEffects,
): Promise<TuiInteractiveCommandResult> {
  const value = request.raw.trim();
  const activitySettings = request.activitySettings ?? DEFAULT_TUI_ACTIVITY_SETTINGS;

  const metaPlan = planTuiMetaCommand(value, { hasPendingLogin: Boolean(request.pendingLogin) });
  if (metaPlan.kind !== "unhandled") {
    switch (metaPlan.kind) {
      case "empty":
        return { logLines: [], pendingLogin: request.pendingLogin };
      case "cancelPendingLogin":
        return { logLines: ["cancelled login prompt"], pendingLogin: null };
      case "exit":
        return { logLines: [], pendingLogin: null, shouldExit: true };
      case "help":
        return { logLines: formatTuiCommandHelp(), pendingLogin: null };
    }
  }

  if (request.pendingLogin && !value.startsWith("/")) {
    return executeTuiPendingLoginSubmission(
      request.pendingLogin,
      value,
      effects.pendingLogin,
    );
  }

  const activityResult = executeTuiActivityCommandPlan(
    planTuiActivityCommand(value, activitySettings),
    effects.activity,
  );
  if (activityResult) {
    return { ...activityResult, pendingLogin: null };
  }

  const operatorResult = executeTuiOperatorCommandPlan(
    planTuiOperatorCommand(value),
    effects.operator,
  );
  if (operatorResult) {
    return { ...operatorResult, pendingLogin: null };
  }

  const solveResult = await executeTuiSolveCommandPlan(
    planTuiSolveCommand(value),
    effects.solve,
  );
  if (solveResult) {
    return { ...solveResult, pendingLogin: null };
  }

  const startRunResult = await executeTuiStartRunCommandPlan(
    planTuiStartRunCommand(value),
    effects.startRun,
  );
  if (startRunResult) {
    return { ...startRunResult, pendingLogin: null };
  }

  const runInspectionResult = await executeTuiRunInspectionCommandPlan(
    planTuiRunInspectionCommand(value, effects.readActiveRunId),
    effects.runInspection,
  );
  if (runInspectionResult) {
    return { ...runInspectionResult, pendingLogin: null };
  }

  const chatResult = await executeTuiChatCommandPlan(
    planTuiChatCommand(value),
    effects.chat,
  );
  if (chatResult) {
    return { ...chatResult, pendingLogin: null };
  }

  const authPlan = planTuiAuthCommand(value);
  const authStatusResult = executeTuiAuthStatusCommandPlan(authPlan, effects.authStatus);
  if (authStatusResult) {
    return { ...authStatusResult, pendingLogin: null };
  }

  const authLogoutResult = executeTuiAuthLogoutCommandPlan(authPlan, effects.authLogout);
  if (authLogoutResult) {
    return { ...authLogoutResult, pendingLogin: null };
  }

  const authLoginResult = await executeTuiAuthLoginCommandPlan(authPlan, effects.authLogin);
  if (authLoginResult) {
    return authLoginResult;
  }

  return { logLines: ["unknown command; use /help"], pendingLogin: null };
}
