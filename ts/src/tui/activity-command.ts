import {
  DEFAULT_TUI_ACTIVITY_SETTINGS,
  TUI_ACTIVITY_USAGE,
  formatTuiActivitySettings,
  parseTuiActivitySettings,
  type TuiActivitySettings,
} from "./activity-summary.js";

export type TuiActivityCommandResolution =
  | {
      readonly kind: "status";
      readonly settings: TuiActivitySettings;
    }
  | {
      readonly kind: "reset";
    }
  | {
      readonly kind: "update";
      readonly settings: TuiActivitySettings;
    }
  | {
      readonly kind: "invalid";
    };

export type TuiActivityCommandPlan =
  | {
      readonly kind: "unhandled";
    }
  | {
      readonly kind: "read";
      readonly settings: TuiActivitySettings;
    }
  | {
      readonly kind: "reset";
    }
  | {
      readonly kind: "save";
      readonly settings: TuiActivitySettings;
    }
  | {
      readonly kind: "usage";
      readonly usageLine: string;
    };

export interface TuiActivityCommandEffects {
  reset(): TuiActivitySettings;
  save(settings: TuiActivitySettings): void;
}

export interface TuiActivityCommandExecutionResult {
  logLines: string[];
  activitySettings?: TuiActivitySettings;
}

export function resolveTuiActivityCommand(
  raw: string,
  current: TuiActivitySettings = DEFAULT_TUI_ACTIVITY_SETTINGS,
): TuiActivityCommandResolution {
  const value = raw.trim();
  if (value.length === 0 || value === "status") {
    return {
      kind: "status",
      settings: current,
    };
  }

  if (value === "reset") {
    return {
      kind: "reset",
    };
  }

  const settings = parseTuiActivitySettings(value, current);
  if (!settings) {
    return {
      kind: "invalid",
    };
  }
  return {
    kind: "update",
    settings,
  };
}

export function planTuiActivityCommand(
  raw: string,
  current: TuiActivitySettings = DEFAULT_TUI_ACTIVITY_SETTINGS,
): TuiActivityCommandPlan {
  const value = raw.trim();
  if (value !== "/activity" && !value.startsWith("/activity ")) {
    return {
      kind: "unhandled",
    };
  }

  const resolution = resolveTuiActivityCommand(value.slice("/activity".length), current);
  switch (resolution.kind) {
    case "status":
      return {
        kind: "read",
        settings: resolution.settings,
      };
    case "reset":
      return {
        kind: "reset",
      };
    case "update":
      return {
        kind: "save",
        settings: resolution.settings,
      };
    case "invalid":
      return {
        kind: "usage",
        usageLine: `usage: ${TUI_ACTIVITY_USAGE}`,
      };
  }
}

export function executeTuiActivityCommandPlan(
  plan: TuiActivityCommandPlan,
  effects: TuiActivityCommandEffects,
): TuiActivityCommandExecutionResult | null {
  switch (plan.kind) {
    case "unhandled":
      return null;
    case "read":
      return {
        logLines: [formatTuiActivitySettings(plan.settings)],
      };
    case "reset": {
      const settings = effects.reset();
      return {
        logLines: [formatTuiActivitySettings(settings)],
        activitySettings: settings,
      };
    }
    case "save":
      effects.save(plan.settings);
      return {
        logLines: [formatTuiActivitySettings(plan.settings)],
        activitySettings: plan.settings,
      };
    case "usage":
      return {
        logLines: [plan.usageLine],
      };
  }
}
