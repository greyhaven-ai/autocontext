export const TUI_CHAT_USAGE = "chat command requires a role and message";

export type TuiChatCommandPlan =
  | {
      readonly kind: "unhandled";
    }
  | {
      readonly kind: "usage";
      readonly usageLine: string;
    }
  | {
      readonly kind: "chat";
      readonly role: string;
      readonly message: string;
    };

export interface TuiChatCommandEffects {
  chatAgent(role: string, message: string): Promise<string>;
}

export interface TuiChatCommandExecutionResult {
  logLines: string[];
}

export function planTuiChatCommand(raw: string): TuiChatCommandPlan {
  const value = raw.trim();
  if (!value.startsWith("/chat ")) {
    return {
      kind: "unhandled",
    };
  }

  const [, role = "analyst", ...rest] = value.split(/\s+/);
  const message = rest.join(" ").trim();
  if (!message) {
    return {
      kind: "usage",
      usageLine: TUI_CHAT_USAGE,
    };
  }

  return {
    kind: "chat",
    role,
    message,
  };
}

export function formatTuiChatResponseLine(role: string, response: string): string {
  const firstLine = response.split("\n")[0] ?? response;
  return `[${role}] ${firstLine}`;
}

export async function executeTuiChatCommandPlan(
  plan: TuiChatCommandPlan,
  effects: TuiChatCommandEffects,
): Promise<TuiChatCommandExecutionResult | null> {
  switch (plan.kind) {
    case "unhandled":
      return null;
    case "usage":
      return { logLines: [plan.usageLine] };
    case "chat":
      try {
        const response = await effects.chatAgent(plan.role, plan.message);
        return {
          logLines: [formatTuiChatResponseLine(plan.role, response)],
        };
      } catch (err) {
        return { logLines: [err instanceof Error ? err.message : String(err)] };
      }
  }
}
