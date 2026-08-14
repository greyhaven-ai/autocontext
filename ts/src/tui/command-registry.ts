import type { SlashCommand } from "./pi-tui-adapter.js";

export type TuiCommandRoute =
  | "meta"
  | "run-control"
  | "inspection"
  | "cockpit"
  | "agent"
  | "auth"
  | "scenario"
  | "settings";

interface TuiCommandDefinition {
  readonly name: string;
  readonly aliases: readonly string[];
  readonly argumentHint?: string;
  readonly summary: string;
  readonly route: TuiCommandRoute;
  readonly requiredCapability?: string;
  readonly unavailableReason?: string;
  readonly keybindings?: readonly string[];
  readonly destructive?: boolean;
}

const command = <const T extends TuiCommandDefinition>(
  definition: T,
): TuiCommandDefinition & T & { readonly executor: T["name"] } => ({
  ...definition,
  executor: definition.name,
});

/** The sole metadata registry used by help, completion, routing, and key hints. */
export const TUI_COMMAND_REGISTRY = [
  command({ name: "help", aliases: ["?"], summary: "Show commands and keybindings", route: "meta", keybindings: ["F1"] }),
  command({ name: "quit", aliases: ["exit", "detach"], summary: "Detach this TUI without stopping the run", route: "meta", keybindings: ["Ctrl+C"] }),
  command({ name: "stop", aliases: [], argumentHint: "confirm", summary: "Cooperatively stop the active run after confirmation", route: "run-control", requiredCapability: "safe_run_stop_v1", destructive: true }),
  command({ name: "run", aliases: [], argumentHint: "<scenario> [iterations]", summary: "Start a scenario (iterations must be positive)", route: "run-control" }),
  command({ name: "pause", aliases: [], summary: "Pause the attached run", route: "run-control" }),
  command({ name: "resume", aliases: [], summary: "Resume the attached run", route: "run-control" }),
  command({ name: "hint", aliases: [], argumentHint: "<text>", summary: "Queue an operator hint", route: "run-control" }),
  command({ name: "gate", aliases: [], argumentHint: "<advance|retry|rollback>", summary: "Override the next gate decision", route: "run-control" }),
  command({ name: "status", aliases: [], argumentHint: "[run-id]", summary: "Inspect run and runtime-session status", route: "inspection" }),
  command({ name: "show", aliases: [], argumentHint: "[run-id] [--best]", summary: "Inspect a run and its generations", route: "inspection" }),
  command({ name: "artifacts", aliases: [], argumentHint: "[run-id]", summary: "Discover run findings, timeline, and writeup resources", route: "inspection" }),
  command({ name: "export", aliases: [], argumentHint: "[run-id]", summary: "Discover the canonical export resource for a run", route: "inspection" }),
  command({ name: "watch", aliases: ["follow"], argumentHint: "[run-id]", summary: "Follow a run until terminal state or detach", route: "inspection" }),
  command({ name: "timeline", aliases: [], argumentHint: "[run-id]", summary: "Inspect the durable runtime timeline", route: "inspection" }),
  command({ name: "findings", aliases: ["trace-gates"], argumentHint: "[run-id]", summary: "Inspect trace findings and gate decisions", route: "inspection" }),
  command({ name: "approve", aliases: [], argumentHint: "<scenario> confirm", summary: "Approve a pending playbook after explicit confirmation", route: "scenario", destructive: true }),
  command({ name: "reject", aliases: [], argumentHint: "<scenario> confirm", summary: "Reject a pending playbook after explicit confirmation", route: "scenario", destructive: true }),
  command({ name: "runs", aliases: ["list"], summary: "Browse active and recent runs", route: "cockpit" }),
  command({ name: "queue", aliases: [], summary: "Show waiting, retry, running, stale, and terminal tasks", route: "cockpit" }),
  command({ name: "workers", aliases: [], summary: "Show worker liveness and current work", route: "cockpit" }),
  command({ name: "sessions", aliases: ["runtime-sessions"], summary: "Browse background and runtime sessions", route: "cockpit" }),
  command({ name: "session", aliases: [], argumentHint: "<session-id>", summary: "Inspect a background or runtime session and its relationships", route: "cockpit" }),
  command({ name: "chat", aliases: [], argumentHint: "<role> <message>", summary: "Chat with an agent without truncating multiline output", route: "agent" }),
  command({ name: "solve", aliases: [], argumentHint: "<plain-language goal>", summary: "Create, confirm, and run an agent-task scenario", route: "scenario" }),
  command({ name: "scenarios", aliases: [], summary: "List scenarios with origin and availability", route: "scenario" }),
  command({ name: "routing", aliases: [], summary: "Show provider, model, hosting, and capability tier", route: "scenario" }),
  command({ name: "login", aliases: [], argumentHint: "<provider> [model] [baseUrl]", summary: "Enter credentials using the masked prompt", route: "auth" }),
  command({ name: "logout", aliases: [], argumentHint: "[provider]", summary: "Clear stored credentials", route: "auth" }),
  command({ name: "provider", aliases: [], argumentHint: "<name>", summary: "Switch the active provider", route: "auth" }),
  command({ name: "whoami", aliases: [], summary: "Show authentication and routing state", route: "auth" }),
  command({ name: "activity", aliases: [], argumentHint: "[status|reset|<all|runtime|prompts|commands|children|errors> [quiet|normal|verbose]]", summary: "Configure transcript activity filters", route: "settings" }),
] as const satisfies readonly (TuiCommandDefinition & { readonly executor: string })[];

export type TuiCommandDescriptor = (typeof TUI_COMMAND_REGISTRY)[number];
export type TuiCommandExecutor = TuiCommandDescriptor["executor"];

const COMMAND_BY_NAME = new Map<string, TuiCommandDescriptor>();
for (const descriptor of TUI_COMMAND_REGISTRY) {
  COMMAND_BY_NAME.set(descriptor.name, descriptor);
  for (const alias of descriptor.aliases) COMMAND_BY_NAME.set(alias, descriptor);
}

export interface ResolvedTuiCommand {
  readonly descriptor: TuiCommandDescriptor;
  readonly invokedAs: string;
  readonly args: string;
}

export function resolveTuiCommand(raw: string): ResolvedTuiCommand | null {
  const value = raw.trim();
  const match = value.match(/^\/([^\s]+)(?:\s+([\s\S]*))?$/);
  if (!match) return null;
  const invokedAs = match[1]!.toLowerCase();
  const descriptor = COMMAND_BY_NAME.get(invokedAs);
  return descriptor
    ? { descriptor, invokedAs, args: match[2]?.trim() ?? "" }
    : null;
}

export function formatTuiCommandHelp(capabilities: readonly string[] = []): string[] {
  const available = new Set(capabilities);
  const width = Math.max(...TUI_COMMAND_REGISTRY.map((entry) => commandUsage(entry).length));
  const lines = [
    "Commands (Tab completes; aliases are shown in parentheses):",
    ...TUI_COMMAND_REGISTRY.map((entry) => {
      const aliases = entry.aliases.length ? ` (${entry.aliases.map((alias) => `/${alias}`).join(", ")})` : "";
      const keybindings = entry.keybindings?.length ? ` · ${entry.keybindings.join(", ")}` : "";
      const unavailable = entry.requiredCapability && !available.has(entry.requiredCapability)
        ? ` · unavailable: ${entry.unavailableReason ?? `server lacks ${entry.requiredCapability}`}`
        : "";
      return `  ${commandUsage(entry).padEnd(width)}  ${entry.summary}${aliases}${keybindings}${unavailable}`;
    }),
    "Keys: PageUp/PageDown or mouse scroll · Ctrl+Shift+F search · End follow tail · Shift+Enter newline",
  ];
  return lines;
}

export function tuiSlashCommands(capabilities: readonly string[] = []): SlashCommand[] {
  const available = new Set(capabilities);
  return TUI_COMMAND_REGISTRY.map((entry) => ({
    name: entry.name,
    description: entry.requiredCapability && !available.has(entry.requiredCapability)
      ? `${entry.summary} (unavailable: ${entry.unavailableReason ?? `server lacks ${entry.requiredCapability}`})`
      : entry.summary,
    ...(entry.argumentHint ? { argumentHint: entry.argumentHint } : {}),
  }));
}

export function assertTuiCommandAvailable(
  descriptor: TuiCommandDescriptor,
  capabilities: readonly string[],
): void {
  if (!descriptor.requiredCapability || capabilities.includes(descriptor.requiredCapability)) return;
  throw new Error(
    descriptor.unavailableReason
      ?? `/${descriptor.name} is unavailable because the server lacks ${descriptor.requiredCapability}`,
  );
}

function commandUsage(entry: TuiCommandDescriptor): string {
  return `/${entry.name}${entry.argumentHint ? ` ${entry.argumentHint}` : ""}`;
}
