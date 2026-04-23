type CommandGroup = "primary" | "control-plane" | "python-only";

interface CommandDescriptor {
  name: string;
  description: string;
  group: CommandGroup;
  route: CliCommandRoute;
  visible?: boolean;
}

export type NoDbCommandName =
  | "init"
  | "capabilities"
  | "login"
  | "whoami"
  | "logout"
  | "providers"
  | "models"
  | "train"
  | "simulate"
  | "investigate"
  | "analyze"
  | "blob"
  | "production-traces"
  | "instrument";

export type DbCommandName =
  | "mission"
  | "campaign"
  | "run"
  | "list"
  | "replay"
  | "benchmark"
  | "export"
  | "export-training-data"
  | "import-package"
  | "new-scenario"
  | "tui"
  | "judge"
  | "improve"
  | "repl"
  | "queue"
  | "status"
  | "serve"
  | "mcp-serve";

export type ControlPlaneCommandName =
  | "candidate"
  | "eval"
  | "promotion"
  | "registry"
  | "emit-pr";

export type CliCommandRoute =
  | { kind: "no-db"; command: NoDbCommandName }
  | { kind: "db"; command: DbCommandName }
  | { kind: "control-plane"; command: ControlPlaneCommandName }
  | { kind: "version"; command: "version" }
  | { kind: "python-only"; command: string }
  | { kind: "unknown"; command: string };

const COMMANDS: readonly CommandDescriptor[] = [
  { name: "init", description: "Scaffold project config and AGENTS guidance", group: "primary", route: { kind: "no-db", command: "init" } },
  { name: "run", description: "Run generation loop for a scenario", group: "primary", route: { kind: "db", command: "run" } },
  { name: "list", description: "List recent runs", group: "primary", route: { kind: "db", command: "list" } },
  { name: "replay", description: "Print replay JSON for a generation", group: "primary", route: { kind: "db", command: "replay" } },
  { name: "benchmark", description: "Run benchmark (multiple runs, aggregate stats)", group: "primary", route: { kind: "db", command: "benchmark" } },
  { name: "export", description: "Export strategy package for a scenario", group: "primary", route: { kind: "db", command: "export" } },
  { name: "export-training-data", description: "Export training data as JSONL", group: "primary", route: { kind: "db", command: "export-training-data" } },
  { name: "import-package", description: "Import a strategy package from file", group: "primary", route: { kind: "db", command: "import-package" } },
  { name: "new-scenario", description: "Create or scaffold a scenario", group: "primary", route: { kind: "db", command: "new-scenario" } },
  { name: "capabilities", description: "Show available scenarios, providers, and features (JSON)", group: "primary", route: { kind: "no-db", command: "capabilities" } },
  { name: "login", description: "Store provider credentials persistently", group: "primary", route: { kind: "no-db", command: "login" } },
  { name: "whoami", description: "Show current auth status and provider", group: "primary", route: { kind: "no-db", command: "whoami" } },
  { name: "logout", description: "Clear stored provider credentials", group: "primary", route: { kind: "no-db", command: "logout" } },
  { name: "providers", description: "List all known providers with auth status (JSON)", group: "primary", route: { kind: "no-db", command: "providers" } },
  { name: "models", description: "List available models for authenticated providers (JSON)", group: "primary", route: { kind: "no-db", command: "models" } },
  { name: "mission", description: "Manage multi-step task missions", group: "primary", route: { kind: "db", command: "mission" } },
  { name: "campaign", description: "Manage multi-mission campaigns", group: "primary", route: { kind: "db", command: "campaign" } },
  { name: "tui", description: "Start interactive TUI (WebSocket server + Ink UI)", group: "primary", route: { kind: "db", command: "tui" } },
  { name: "judge", description: "One-shot evaluation of output against a rubric", group: "primary", route: { kind: "db", command: "judge" } },
  { name: "improve", description: "Run multi-round improvement loop", group: "primary", route: { kind: "db", command: "improve" } },
  { name: "repl", description: "Run a direct REPL-loop session", group: "primary", route: { kind: "db", command: "repl" } },
  { name: "queue", description: "Add a task to the background runner queue", group: "primary", route: { kind: "db", command: "queue" } },
  { name: "status", description: "Show queue status", group: "primary", route: { kind: "db", command: "status" } },
  { name: "serve", description: "Start HTTP API server [--json]", group: "primary", route: { kind: "db", command: "serve" } },
  { name: "train", description: "Train a distilled model from curated dataset (requires configured executor)", group: "primary", route: { kind: "no-db", command: "train" } },
  { name: "simulate", description: "Run a plain-language simulation with sweeps and analysis", group: "primary", route: { kind: "no-db", command: "simulate" } },
  { name: "investigate", description: "Run a plain-language investigation with evidence and hypotheses", group: "primary", route: { kind: "no-db", command: "investigate" } },
  { name: "analyze", description: "Analyze and compare runs, simulations, investigations, and missions", group: "primary", route: { kind: "no-db", command: "analyze" } },
  { name: "mcp-serve", description: "Start MCP server on stdio", group: "primary", route: { kind: "db", command: "mcp-serve" } },
  { name: "version", description: "Show version", group: "primary", route: { kind: "version", command: "version" } },
  { name: "blob", description: "Manage blob artifacts", group: "primary", route: { kind: "no-db", command: "blob" }, visible: false },
  { name: "candidate", description: "Register/list/show/lineage/rollback control-plane artifacts", group: "control-plane", route: { kind: "control-plane", command: "candidate" } },
  { name: "eval", description: "Attach/list EvalRuns on artifacts", group: "control-plane", route: { kind: "control-plane", command: "eval" } },
  { name: "promotion", description: "Decide/apply/history for promotion transitions", group: "control-plane", route: { kind: "control-plane", command: "promotion" } },
  { name: "registry", description: "Repair/validate/migrate the control-plane registry", group: "control-plane", route: { kind: "control-plane", command: "registry" } },
  { name: "emit-pr", description: "Generate a promotion PR (or dry-run bundle) for a candidate", group: "control-plane", route: { kind: "control-plane", command: "emit-pr" } },
  { name: "production-traces", description: "Ingest/list/show/stats/build-dataset/export/policy/rotate-salt/prune (Foundation A — AC-539)", group: "control-plane", route: { kind: "no-db", command: "production-traces" } },
  { name: "instrument", description: "Scan a repo for LLM clients and propose/apply Autocontext wrappers (A2-I — AC-540)", group: "control-plane", route: { kind: "no-db", command: "instrument" } },
  { name: "ecosystem", description: "", group: "python-only", route: { kind: "python-only", command: "ecosystem" } },
  { name: "ab-test", description: "", group: "python-only", route: { kind: "python-only", command: "ab-test" } },
  { name: "resume", description: "", group: "python-only", route: { kind: "python-only", command: "resume" } },
  { name: "wait", description: "", group: "python-only", route: { kind: "python-only", command: "wait" } },
  { name: "trigger-distillation", description: "", group: "python-only", route: { kind: "python-only", command: "trigger-distillation" } },
];

const COMMAND_ROUTE_BY_NAME = new Map(COMMANDS.map((command) => [command.name, command.route]));

export function resolveCliCommand(command: string): CliCommandRoute {
  return COMMAND_ROUTE_BY_NAME.get(command) ?? { kind: "unknown", command };
}

export function buildCliHelp(): string {
  return `
autoctx — always-on agent evaluation harness

Commands:
${formatCommandList("primary")}

Control plane (Layer 7-9):
${formatCommandList("control-plane")}

Python-only commands (not supported in npm package):
  ${visibleCommands("python-only").map((command) => command.name).join(", ")}

Run \`autoctx <command> --help\` for command-specific options.

Install: npm install -g autoctx
Note: The npm package is \`autoctx\`, not \`autocontext\` (different package).
`.trim();
}

export function visibleCommandNames(): string[] {
  return COMMANDS.filter((command) => command.visible !== false).map((command) => command.name);
}

function visibleCommands(group: CommandGroup): CommandDescriptor[] {
  return COMMANDS.filter((command) => command.group === group && command.visible !== false);
}

function formatCommandList(group: CommandGroup): string {
  const commands = visibleCommands(group);
  const width = Math.max(...commands.map((command) => command.name.length)) + 2;
  return commands
    .map((command) => `  ${command.name.padEnd(width)}${command.description}`)
    .join("\n");
}
