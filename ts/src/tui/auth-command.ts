export const TUI_LOGIN_USAGE = "usage: /login <provider> [apiKey] [model] [baseUrl]";
export const TUI_PROVIDER_USAGE = "usage: /provider <name>";

export interface TuiAuthStatusForDisplay {
  readonly provider: string;
  readonly authenticated: boolean;
  readonly model?: string;
  readonly configuredProviders?: readonly {
    readonly provider: string;
  }[];
}

export type TuiAuthCommandPlan =
  | {
      readonly kind: "unhandled";
    }
  | {
      readonly kind: "usage";
      readonly usageLine: string;
    }
  | {
      readonly kind: "login";
      readonly provider: string;
      readonly apiKey?: string;
      readonly model?: string;
      readonly baseUrl?: string;
    }
  | {
      readonly kind: "logout";
      readonly provider?: string;
    }
  | {
      readonly kind: "switchProvider";
      readonly provider: string;
    }
  | {
      readonly kind: "whoami";
    };

export interface TuiAuthStatusCommandEffects {
  selectProvider(provider: string): TuiAuthStatusForDisplay;
  readWhoami(preferredProvider?: string): TuiAuthStatusForDisplay;
  getActiveProvider(): string | null | undefined;
}

export interface TuiAuthStatusCommandExecutionResult {
  logLines: string[];
}

export interface TuiAuthLogoutCommandEffects {
  logout(provider?: string): void;
  clearActiveProvider(): void;
  getActiveProvider(): string | null | undefined;
  selectProvider(preferredProvider?: string): TuiAuthStatusForDisplay;
  readWhoami(preferredProvider?: string): TuiAuthStatusForDisplay;
}

export interface TuiAuthLogoutCommandExecutionResult {
  logLines: string[];
}

export interface TuiPendingLoginState {
  provider: string;
  model?: string;
  baseUrl?: string;
}

export interface TuiAuthLoginResult {
  saved: boolean;
  provider: string;
  validationWarning?: string;
}

export interface TuiAuthLoginCommandEffects {
  providerRequiresKey(provider: string): boolean;
  login(
    provider: string,
    apiKey?: string,
    model?: string,
    baseUrl?: string,
  ): Promise<TuiAuthLoginResult>;
  selectProvider(provider: string): TuiAuthStatusForDisplay;
}

export interface TuiAuthLoginCommandExecutionResult {
  logLines: string[];
  pendingLogin: TuiPendingLoginState | null;
}

export function planTuiAuthCommand(raw: string): TuiAuthCommandPlan {
  const value = raw.trim();

  if (isTuiCommand(value, "/login")) {
    const [, providerRaw, apiKey, model, baseUrl] = value.split(/\s+/, 5);
    const provider = normalizeTuiProvider(providerRaw);
    if (!provider) {
      return {
        kind: "usage",
        usageLine: TUI_LOGIN_USAGE,
      };
    }
    return {
      kind: "login",
      provider,
      ...(apiKey ? { apiKey } : {}),
      ...(model ? { model } : {}),
      ...(baseUrl ? { baseUrl } : {}),
    };
  }

  if (isTuiCommand(value, "/logout")) {
    const [, providerRaw] = value.split(/\s+/, 2);
    const provider = normalizeTuiProvider(providerRaw);
    return {
      kind: "logout",
      ...(provider ? { provider } : {}),
    };
  }

  if (value === "/provider") {
    return {
      kind: "usage",
      usageLine: TUI_PROVIDER_USAGE,
    };
  }

  if (value.startsWith("/provider ")) {
    const [, providerRaw] = value.split(/\s+/, 2);
    const provider = normalizeTuiProvider(providerRaw);
    if (!provider) {
      return {
        kind: "usage",
        usageLine: TUI_PROVIDER_USAGE,
      };
    }
    return {
      kind: "switchProvider",
      provider,
    };
  }

  if (value === "/whoami") {
    return {
      kind: "whoami",
    };
  }

  return {
    kind: "unhandled",
  };
}

export function formatTuiWhoamiLines(status: TuiAuthStatusForDisplay): string[] {
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

export function executeTuiAuthStatusCommandPlan(
  plan: TuiAuthCommandPlan,
  effects: TuiAuthStatusCommandEffects,
): TuiAuthStatusCommandExecutionResult | null {
  switch (plan.kind) {
    case "usage":
      return { logLines: [plan.usageLine] };
    case "switchProvider": {
      const selected = effects.selectProvider(plan.provider);
      return {
        logLines: [
          `active provider: ${selected.provider}`,
          ...formatTuiWhoamiLines(effects.readWhoami(selected.provider)),
        ],
      };
    }
    case "whoami":
      return {
        logLines: formatTuiWhoamiLines(
          effects.readWhoami(effects.getActiveProvider() ?? undefined),
        ),
      };
    case "unhandled":
    case "login":
    case "logout":
      return null;
  }
}

export function executeTuiAuthLogoutCommandPlan(
  plan: TuiAuthCommandPlan,
  effects: TuiAuthLogoutCommandEffects,
): TuiAuthLogoutCommandExecutionResult | null {
  if (plan.kind !== "logout") {
    return null;
  }

  effects.logout(plan.provider);
  if (!plan.provider) {
    effects.clearActiveProvider();
    return {
      logLines: [
        "cleared stored credentials",
        ...formatTuiWhoamiLines(effects.readWhoami()),
      ],
    };
  }

  const activeProvider = effects.getActiveProvider();
  const status = effects.selectProvider(
    activeProvider === plan.provider ? plan.provider : activeProvider ?? undefined,
  );
  return {
    logLines: [`logged out of ${plan.provider}`, ...formatTuiWhoamiLines(status)],
  };
}

export async function executeTuiAuthLoginCommandPlan(
  plan: TuiAuthCommandPlan,
  effects: TuiAuthLoginCommandEffects,
): Promise<TuiAuthLoginCommandExecutionResult | null> {
  if (plan.kind !== "login") {
    return null;
  }

  if (!plan.apiKey && effects.providerRequiresKey(plan.provider)) {
    return {
      logLines: [`enter API key for ${plan.provider} on the next line, or /cancel`],
      pendingLogin: {
        provider: plan.provider,
        ...(plan.model ? { model: plan.model } : {}),
        ...(plan.baseUrl ? { baseUrl: plan.baseUrl } : {}),
      },
    };
  }

  return saveTuiLogin({
    provider: plan.provider,
    apiKey: plan.apiKey,
    model: plan.model,
    baseUrl: plan.baseUrl,
  }, effects, null);
}

export async function executeTuiPendingLoginSubmission(
  pendingLogin: TuiPendingLoginState,
  apiKey: string,
  effects: Pick<TuiAuthLoginCommandEffects, "login" | "selectProvider">,
): Promise<TuiAuthLoginCommandExecutionResult> {
  return saveTuiLogin({
    provider: pendingLogin.provider,
    apiKey,
    model: pendingLogin.model,
    baseUrl: pendingLogin.baseUrl,
  }, effects, pendingLogin);
}

async function saveTuiLogin(
  request: {
    provider: string;
    apiKey?: string;
    model?: string;
    baseUrl?: string;
  },
  effects: Pick<TuiAuthLoginCommandEffects, "login" | "selectProvider">,
  pendingLoginOnFailure: TuiPendingLoginState | null,
): Promise<TuiAuthLoginCommandExecutionResult> {
  const loginResult = await effects.login(
    request.provider,
    request.apiKey,
    request.model,
    request.baseUrl,
  );
  if (!loginResult.saved) {
    return {
      logLines: [loginResult.validationWarning ?? `Unable to log in to ${request.provider}`],
      pendingLogin: pendingLoginOnFailure,
    };
  }

  const status = effects.selectProvider(loginResult.provider);
  const logLines = [`logged in to ${status.provider}`];
  if (loginResult.validationWarning) {
    logLines.push(`warning: ${loginResult.validationWarning}`);
  }
  return { logLines, pendingLogin: null };
}

function isTuiCommand(value: string, command: string): boolean {
  return value === command || value.startsWith(`${command} `);
}

function normalizeTuiProvider(provider?: string): string | undefined {
  const normalized = provider?.trim().toLowerCase();
  return normalized || undefined;
}
