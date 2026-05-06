import { TUI_ACTIVITY_USAGE } from "./activity-summary.js";

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
  return [
    '/solve "plain-language goal"',
    "/run <scenario> [iterations]",
    "/status <run-id>",
    "/show <run-id> --best",
    "/watch <run-id>",
    "/timeline <run-id>",
    TUI_ACTIVITY_USAGE,
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
