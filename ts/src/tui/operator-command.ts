export type TuiOperatorCommandPlan =
  | {
      readonly kind: "unhandled";
    }
  | {
      readonly kind: "pause";
    }
  | {
      readonly kind: "resume";
    }
  | {
      readonly kind: "listScenarios";
    }
  | {
      readonly kind: "injectHint";
      readonly text: string;
    }
  | {
      readonly kind: "overrideGate";
      readonly decision: TuiGateOverrideDecision;
    }
  | {
      readonly kind: "invalidGate";
    };

export type TuiGateOverrideDecision = "advance" | "retry" | "rollback";

export interface TuiOperatorCommandEffects {
  pause(): void;
  resume(): void;
  listScenarios(): readonly string[];
  injectHint(text: string): void;
  overrideGate(decision: TuiGateOverrideDecision): void;
}

export interface TuiOperatorCommandExecutionResult {
  readonly logLines: string[];
}

export function planTuiOperatorCommand(raw: string): TuiOperatorCommandPlan {
  const value = raw.trim();
  switch (value) {
    case "/pause":
      return {
        kind: "pause",
      };
    case "/resume":
      return {
        kind: "resume",
      };
    case "/scenarios":
      return {
        kind: "listScenarios",
      };
  }

  if (value.startsWith("/hint ")) {
    return {
      kind: "injectHint",
      text: value.slice("/hint ".length).trim(),
    };
  }

  if (value.startsWith("/gate ")) {
    const decision = value.slice("/gate ".length).trim();
    if (isTuiGateOverrideDecision(decision)) {
      return {
        kind: "overrideGate",
        decision,
      };
    }
    return {
      kind: "invalidGate",
    };
  }

  return {
    kind: "unhandled",
  };
}

export function executeTuiOperatorCommandPlan(
  plan: TuiOperatorCommandPlan,
  effects: TuiOperatorCommandEffects,
): TuiOperatorCommandExecutionResult | null {
  switch (plan.kind) {
    case "pause":
      effects.pause();
      return {
        logLines: ["paused active loop"],
      };
    case "resume":
      effects.resume();
      return {
        logLines: ["resumed active loop"],
      };
    case "listScenarios":
      return {
        logLines: [formatTuiScenarioList(effects.listScenarios())],
      };
    case "injectHint":
      effects.injectHint(plan.text);
      return {
        logLines: ["operator hint queued"],
      };
    case "overrideGate":
      effects.overrideGate(plan.decision);
      return {
        logLines: [`gate override queued: ${plan.decision}`],
      };
    case "invalidGate":
      return {
        logLines: ["gate override must be advance|retry|rollback"],
      };
    case "unhandled":
      return null;
  }
}

export function formatTuiScenarioList(scenarios: readonly string[]): string {
  return `scenarios: ${scenarios.join(", ")}`;
}

function isTuiGateOverrideDecision(value: string): value is TuiGateOverrideDecision {
  return value === "advance" || value === "retry" || value === "rollback";
}
