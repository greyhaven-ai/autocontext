/**
 * `serve` and `mcp-serve` commands (AC-853 split of command-handlers.ts).
 */
import { parseArgs } from "node:util";
import { resolve } from "node:path";
import { asDbPath } from "../../domain/ids.js";
import { getMigrationsDir, getProvider } from "./shared.js";

export async function cmdServeHttp(dbPath: string): Promise<void> {
  // AC-697 slice 6: detect `autoctx serve mcp` subcommand before
  // the existing serve parseArgs runs. The MCP path routes through
  // the same handler that backs the legacy top-level `mcp-serve`
  // command, so users get the same MCP server behavior at the
  // canonical path the slice-1 contract pins.
  const subArgs = process.argv.slice(3);
  if (subArgs[0] === "mcp") {
    // PR #1001 review (P3): the slice-6 delegation routed
    // `autoctx serve mcp --help` through cmdMcpServe, which
    // printed help under the legacy `autoctx mcp-serve` header.
    // Intercept --help here so users asking about the canonical
    // command name see canonical text. Same pattern as PR #999's
    // `scenario create --help` fix.
    const mcpArgs = subArgs.slice(1);
    if (mcpArgs.includes("--help") || mcpArgs.includes("-h")) {
      const { SERVE_MCP_HELP_TEXT } = await import("../mcp-serve-command-workflow.js");
      console.log(SERVE_MCP_HELP_TEXT);
      process.exit(0);
    }
    // Rewrite argv so cmdMcpServe sees its sub-args at the
    // canonical position (process.argv.slice(3)). Same trick as
    // slice-4 `scenario create` -> `new-scenario`.
    process.argv = [...process.argv.slice(0, 2), "mcp-serve", ...mcpArgs];
    return cmdMcpServe(dbPath);
  }

  const { values } = parseArgs({
    args: subArgs,
    options: {
      port: { type: "string", default: "8000" },
      host: { type: "string", default: "127.0.0.1" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const { planServeCommand, renderServeStartup, SERVE_HELP_TEXT } =
    await import("../serve-command-workflow.js");

  if (values.help) {
    console.log(SERVE_HELP_TEXT);
    process.exit(0);
  }

  const plan = planServeCommand(values);

  const { RunManager, InteractiveServer } = await import("../../server/index.js");
  const { loadSettings } = await import("../../config/index.js");
  const settings = loadSettings();

  const mgr = new RunManager({
    dbPath,
    migrationsDir: getMigrationsDir(),
    runsRoot: resolve(settings.runsRoot),
    knowledgeRoot: resolve(settings.knowledgeRoot),
    skillsRoot: resolve(settings.skillsRoot),
    providerType: settings.agentProvider,
  });
  const server = new InteractiveServer({
    runManager: mgr,
    port: plan.port,
    host: plan.host,
  });
  await server.start();

  const startupInfo = {
    url: `http://${plan.host}:${server.port}`,
    apiUrl: `http://${plan.host}:${server.port}/api/runs`,
    wsUrl: `ws://${plan.host}:${server.port}/ws/interactive`,
    host: plan.host,
    port: server.port,
    scenarios: mgr.listScenarios(),
  };

  for (const line of renderServeStartup(startupInfo, plan.json)) {
    console.log(line);
  }

  await new Promise<void>((res) => {
    const cleanup = () => {
      process.off("SIGINT", cleanup);
      process.off("SIGTERM", cleanup);
      res();
    };
    process.on("SIGINT", cleanup);
    process.on("SIGTERM", cleanup);
  });
  await server.stop();
}

export async function cmdMcpServe(dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      help: { type: "boolean", short: "h" },
    },
  });

  const { buildMcpServeRequest, MCP_SERVE_HELP_TEXT } =
    await import("../mcp-serve-command-workflow.js");

  if (values.help) {
    console.log(MCP_SERVE_HELP_TEXT);
    process.exit(0);
  }

  const { SQLiteStore } = await import("../../storage/index.js");
  const { startServer } = await import("../../mcp/server.js");
  const { loadSettings } = await import("../../config/index.js");

  const store = new SQLiteStore(asDbPath(dbPath));
  store.migrate(getMigrationsDir());

  const { provider, model } = await getProvider();
  const settings = loadSettings();

  try {
    await startServer(
      buildMcpServeRequest({
        store,
        provider,
        model,
        dbPath,
        runsRoot: resolve(settings.runsRoot),
        knowledgeRoot: resolve(settings.knowledgeRoot),
      }),
    );
  } finally {
    provider.close?.();
  }
}
