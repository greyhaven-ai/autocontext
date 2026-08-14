import { formatTuiCommandHelp as formatRegisteredTuiCommandHelp } from "./command-registry.js";

export interface TuiMetaCommandContext {
  readonly hasPendingLogin: boolean;
}

export type TuiMetaCommandPlan =
  | {
      readonly kind: "unhandled";
    }
  | {
      readonly kind: "empty";
    }
  | {
      readonly kind: "help";
    }
  | {
      readonly kind: "exit";
    }
  | {
      readonly kind: "cancelPendingLogin";
    };

export function planTuiMetaCommand(
  raw: string,
  context: TuiMetaCommandContext,
): TuiMetaCommandPlan {
  const value = raw.trim();
  if (!value) {
    return {
      kind: "empty",
    };
  }

  switch (value) {
    case "/help":
      return {
        kind: "help",
      };
    case "/quit":
    case "/exit":
      return {
        kind: "exit",
      };
    case "/cancel":
      return context.hasPendingLogin
        ? {
            kind: "cancelPendingLogin",
          }
        : {
            kind: "unhandled",
          };
    default:
      return {
        kind: "unhandled",
      };
  }
}

export function formatTuiCommandHelp(): string[] {
  return formatRegisteredTuiCommandHelp();
}
