/**
 * `init` and `capabilities` commands (AC-853 split of command-handlers.ts).
 */
import { parseArgs } from "node:util";
import { resolve, join } from "node:path";
import { errorMessage, parsePositiveInteger, buildProjectConfigSummary } from "./shared.js";

async function writeAgentsGuide(targetDir: string): Promise<boolean> {
  const { existsSync, readFileSync, writeFileSync } = await import("node:fs");
  const agentsPath = join(targetDir, "AGENTS.md");
  const block = [
    "<!-- AUTOCTX_GUIDE_START -->",
    "## AutoContext",
    "",
    "- Use `autoctx capabilities` to inspect supported commands and project state.",
    "- Use `autoctx whoami` to confirm provider credentials before running evaluations.",
    "- Run `autoctx run` from this directory to use the defaults stored in `.autoctx.json`.",
    "<!-- AUTOCTX_GUIDE_END -->",
  ].join("\n");

  if (existsSync(agentsPath)) {
    const existing = readFileSync(agentsPath, "utf-8");
    const start = existing.indexOf("<!-- AUTOCTX_GUIDE_START -->");
    const end = existing.indexOf("<!-- AUTOCTX_GUIDE_END -->");
    if (start !== -1 && end !== -1 && end > start) {
      const replacementEnd = end + "<!-- AUTOCTX_GUIDE_END -->".length;
      const updated = `${existing.slice(0, start)}${block}${existing.slice(replacementEnd)}`;
      writeFileSync(agentsPath, updated.endsWith("\n") ? updated : updated + "\n", "utf-8");
      return true;
    }
    if (existing.includes("## AutoContext")) {
      return false;
    }
    writeFileSync(agentsPath, `${existing.trimEnd()}\n\n${block}\n`, "utf-8");
    return true;
  }

  writeFileSync(agentsPath, `# Agent Guide\n\n${block}\n`, "utf-8");
  return true;
}

export async function cmdInit(): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      dir: { type: "string", default: "." },
      scenario: { type: "string" },
      provider: { type: "string" },
      model: { type: "string" },
      gens: { type: "string", default: "3" },
      "agents-md": { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const { buildInitSuccessMessages, INIT_HELP_TEXT, planInitCommand } =
    await import("../init-command-workflow.js");

  if (values.help) {
    console.log(INIT_HELP_TEXT);
    process.exit(0);
  }

  const { existsSync, mkdirSync, writeFileSync } = await import("node:fs");
  const { loadPersistedCredentials, loadProjectConfig } = await import("../../config/index.js");
  const { resolveProviderConfig } = await import("../../providers/index.js");

  let plan;
  try {
    const targetDir = resolve(values.dir ?? ".");
    plan = planInitCommand(values, {
      resolvePath: resolve,
      joinPath: join,
      configExists: existsSync(join(targetDir, ".autoctx.json")),
      projectDefaults: loadProjectConfig(targetDir),
      persistedCredentials: loadPersistedCredentials(),
      env: process.env,
      resolveProviderConfig,
      parsePositiveInteger,
    });
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  mkdirSync(plan.targetDir, { recursive: true });
  mkdirSync(join(plan.targetDir, "runs"), { recursive: true });
  mkdirSync(join(plan.targetDir, "knowledge"), { recursive: true });
  writeFileSync(plan.configPath, JSON.stringify(plan.config, null, 2) + "\n", "utf-8");

  const agentsMdUpdated = await writeAgentsGuide(plan.targetDir);

  for (const line of buildInitSuccessMessages({
    configPath: plan.configPath,
    agentsPath: join(plan.targetDir, "AGENTS.md"),
    agentsMdUpdated,
  })) {
    console.log(line);
  }
}

export async function cmdCapabilities(): Promise<void> {
  const { buildCapabilitiesPayload } = await import("../capabilities-command-workflow.js");
  const { getCapabilities } = await import("../../mcp/capabilities.js");
  const projectConfig = await buildProjectConfigSummary();
  const baseCapabilities = getCapabilities();

  console.log(JSON.stringify(buildCapabilitiesPayload(baseCapabilities, projectConfig), null, 2));
}
