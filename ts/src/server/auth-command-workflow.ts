import type { ClientMessage, ServerMessage } from "./protocol.js";
import type {
  ResolvedTuiAuthSelection,
  TuiAuthStatus,
  TuiLoginResult,
} from "./tui-auth.js";

export interface AuthCommandRunManager {
  getActiveProviderType(): string | null;
  setActiveProvider(config: {
    providerType: string;
    apiKey?: string;
    baseUrl?: string;
    model?: string;
  }): void;
  clearActiveProvider(): void;
}

export interface AuthCommandWorkflowDeps {
  resolveConfigDir?: () => string;
  handleTuiLogin?: (
    configDir: string,
    provider: string,
    apiKey?: string,
    model?: string,
    baseUrl?: string,
  ) => Promise<TuiLoginResult>;
  handleTuiLogout?: (configDir: string, provider?: string) => void;
  handleTuiSwitchProvider?: (configDir: string, provider: string) => TuiAuthStatus;
  handleTuiWhoami?: (configDir: string, preferredProvider?: string) => TuiAuthStatus;
  resolveTuiAuthSelection?: (
    configDir: string,
    preferredProvider?: string,
  ) => ResolvedTuiAuthSelection;
}

export function buildAuthStatusMessage(status: TuiAuthStatus): ServerMessage {
  return {
    type: "auth_status",
    provider: status.provider,
    authenticated: status.authenticated,
    ...(status.model ? { model: status.model } : {}),
    ...(status.configuredProviders ? { configuredProviders: status.configuredProviders } : {}),
  };
}

export function applyResolvedAuthSelection(
  runManager: Pick<AuthCommandRunManager, "setActiveProvider" | "clearActiveProvider">,
  selection: ResolvedTuiAuthSelection,
): void {
  if (selection.provider === "none") {
    runManager.clearActiveProvider();
    return;
  }

  runManager.setActiveProvider({
    providerType: selection.provider,
    ...(selection.apiKey ? { apiKey: selection.apiKey } : {}),
    ...(selection.model ? { model: selection.model } : {}),
    ...(selection.baseUrl ? { baseUrl: selection.baseUrl } : {}),
  });
}

export async function executeAuthCommand(opts: {
  command: Extract<
    ClientMessage,
    { type: "login" | "logout" | "switch_provider" | "whoami" }
  >;
  runManager: AuthCommandRunManager;
  deps?: AuthCommandWorkflowDeps;
}): Promise<ServerMessage> {
  const deps = await resolveAuthCommandDeps(opts.deps);
  const configDir = deps.resolveConfigDir();

  switch (opts.command.type) {
    case "login": {
      const loginResult = await deps.handleTuiLogin(
        configDir,
        opts.command.provider,
        opts.command.apiKey,
        opts.command.model,
        opts.command.baseUrl,
      );
      if (!loginResult.saved) {
        throw new Error(loginResult.validationWarning ?? `Unable to log in to ${opts.command.provider}`);
      }
      const selection = deps.resolveTuiAuthSelection(configDir, loginResult.provider);
      if (selection.provider !== "none") {
        applyResolvedAuthSelection(opts.runManager, selection);
      }
      return buildAuthStatusMessage(deps.handleTuiWhoami(configDir, loginResult.provider));
    }
    case "logout": {
      const currentProvider = opts.runManager.getActiveProviderType() ?? undefined;
      const removedProvider = opts.command.provider?.trim().toLowerCase();
      deps.handleTuiLogout(configDir, opts.command.provider);
      if (!opts.command.provider) {
        opts.runManager.clearActiveProvider();
      } else {
        const preferredProvider = currentProvider === removedProvider ? removedProvider : currentProvider;
        applyResolvedAuthSelection(
          opts.runManager,
          deps.resolveTuiAuthSelection(configDir, preferredProvider),
        );
      }
      return buildAuthStatusMessage(
        deps.handleTuiWhoami(
          configDir,
          opts.command.provider
            ? (currentProvider === removedProvider ? removedProvider : currentProvider)
            : undefined,
        ),
      );
    }
    case "switch_provider": {
      const status = deps.handleTuiSwitchProvider(configDir, opts.command.provider);
      applyResolvedAuthSelection(
        opts.runManager,
        deps.resolveTuiAuthSelection(configDir, opts.command.provider),
      );
      return buildAuthStatusMessage(status);
    }
    case "whoami":
      return buildAuthStatusMessage(
        deps.handleTuiWhoami(configDir, opts.runManager.getActiveProviderType() ?? undefined),
      );
    default:
      throw new Error(`Unsupported auth command: ${String((opts.command as { type?: unknown }).type ?? "unknown")}`);
  }
}

async function resolveAuthCommandDeps(
  overrides?: AuthCommandWorkflowDeps,
): Promise<Required<AuthCommandWorkflowDeps>> {
  if (
    overrides?.resolveConfigDir
    && overrides.handleTuiLogin
    && overrides.handleTuiLogout
    && overrides.handleTuiSwitchProvider
    && overrides.handleTuiWhoami
    && overrides.resolveTuiAuthSelection
  ) {
    return {
      resolveConfigDir: overrides.resolveConfigDir,
      handleTuiLogin: overrides.handleTuiLogin,
      handleTuiLogout: overrides.handleTuiLogout,
      handleTuiSwitchProvider: overrides.handleTuiSwitchProvider,
      handleTuiWhoami: overrides.handleTuiWhoami,
      resolveTuiAuthSelection: overrides.resolveTuiAuthSelection,
    };
  }

  const [{ resolveConfigDir }, tuiAuth] = await Promise.all([
    import("../config/index.js"),
    import("./tui-auth.js"),
  ]);

  return {
    resolveConfigDir: overrides?.resolveConfigDir ?? resolveConfigDir,
    handleTuiLogin: overrides?.handleTuiLogin ?? tuiAuth.handleTuiLogin,
    handleTuiLogout: overrides?.handleTuiLogout ?? tuiAuth.handleTuiLogout,
    handleTuiSwitchProvider: overrides?.handleTuiSwitchProvider ?? tuiAuth.handleTuiSwitchProvider,
    handleTuiWhoami: overrides?.handleTuiWhoami ?? tuiAuth.handleTuiWhoami,
    resolveTuiAuthSelection: overrides?.resolveTuiAuthSelection ?? tuiAuth.resolveTuiAuthSelection,
  };
}
