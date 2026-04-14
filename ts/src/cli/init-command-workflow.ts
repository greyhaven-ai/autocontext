export const INIT_HELP_TEXT = `autoctx init — Scaffold project config and AGENTS guidance

Usage: autoctx init [options]

Options:
  --dir <path>         Directory to initialize (default: current directory)
  --scenario <name>    Default scenario (default: grid_ctf)
  --provider <type>    Default provider (default: deterministic)
  --model <name>       Default model for the provider
  --gens N             Default generations per run (default: 3)

Creates .autoctx.json, AGENTS.md, runs/, and knowledge/ directories.

Examples:
  autoctx init
  autoctx init --scenario grid_ctf --provider anthropic --gens 5
  autoctx init --dir ./my-project

See also: run, login, capabilities`;

export interface InitCommandValues {
  dir?: string;
  scenario?: string;
  provider?: string;
  model?: string;
  gens?: string;
  "agents-md"?: boolean;
}

export interface InitPlan {
  targetDir: string;
  configPath: string;
  config: Record<string, unknown>;
}

interface InitProjectDefaults {
  defaultScenario?: string;
  provider?: string;
  model?: string;
}

interface InitPersistedCredentials {
  provider?: string;
  model?: string;
}

export function planInitCommand(
  values: InitCommandValues,
  deps: {
    resolvePath(path: string): string;
    joinPath(...parts: string[]): string;
    configExists: boolean;
    projectDefaults: InitProjectDefaults | null;
    persistedCredentials: InitPersistedCredentials | null;
    env: Record<string, string | undefined>;
    resolveProviderConfig(): { providerType: string; model?: string };
    parsePositiveInteger(raw: string | undefined, label: string): number;
  },
): InitPlan {
  const targetDir = deps.resolvePath(values.dir ?? ".");
  const configPath = deps.joinPath(targetDir, ".autoctx.json");

  if (deps.configExists) {
    throw new Error(`Error: .autoctx.json already exists in ${targetDir}`);
  }

  let detectedProvider =
    values.provider?.trim() ??
    deps.projectDefaults?.provider ??
    deps.env.AUTOCONTEXT_AGENT_PROVIDER?.trim() ??
    deps.env.AUTOCONTEXT_PROVIDER?.trim() ??
    deps.persistedCredentials?.provider;
  let detectedModel =
    values.model?.trim() ??
    deps.projectDefaults?.model ??
    deps.env.AUTOCONTEXT_AGENT_DEFAULT_MODEL?.trim() ??
    deps.env.AUTOCONTEXT_MODEL?.trim() ??
    deps.persistedCredentials?.model;

  try {
    const resolved = deps.resolveProviderConfig();
    detectedProvider = detectedProvider ?? resolved.providerType;
    detectedModel = detectedModel ?? resolved.model;
  } catch {
    detectedProvider = detectedProvider ?? "deterministic";
  }

  const config: Record<string, unknown> = {
    default_scenario:
      values.scenario ?? deps.projectDefaults?.defaultScenario ?? "grid_ctf",
    provider: detectedProvider ?? "deterministic",
    gens: deps.parsePositiveInteger(values.gens ?? "3", "--gens"),
    knowledge_dir: "./knowledge",
    runs_dir: "./runs",
  };

  if (detectedModel) {
    config.model = detectedModel;
  }

  return { targetDir, configPath, config };
}

export function buildInitSuccessMessages(input: {
  configPath: string;
  agentsPath: string;
  agentsMdUpdated: boolean;
}): string[] {
  return [
    `Created ${input.configPath}`,
    input.agentsMdUpdated
      ? `Updated ${input.agentsPath}`
      : "AGENTS.md already contained AutoContext guidance",
  ];
}
