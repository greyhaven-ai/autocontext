#!/usr/bin/env node
/**
 * autocontext CLI — command-line dispatcher for the evaluation harness.
 */

import { buildCliHelp, resolveCliCommand } from "./command-registry.js";
import {
  DB_COMMAND_HANDLERS,
  NO_DB_COMMAND_HANDLERS,
  buildProjectConfigSummary,
  cmdControlPlane,
  formatFatalCliError,
  getDbPath,
} from "./command-handlers.js";

const HELP = buildCliHelp();

async function main(): Promise<void> {
  const command = process.argv[2];

  if (command === "--help" || command === "-h") {
    console.log(HELP);
    process.exit(0);
  }

  // AC-394: Smart no-args — show project status if config exists, suggest init otherwise
  if (!command) {
    const projectConfig = await buildProjectConfigSummary();
    if (projectConfig) {
      console.log(JSON.stringify(projectConfig, null, 2));
    } else {
      console.log(HELP);
      console.log("\nTip: Run `autoctx init` to set up this project with a .autoctx.json config.");
    }
    process.exit(0);
  }

  if (command === "--version") {
    const pkg = await import("../../package.json", { with: { type: "json" } });
    console.log(pkg.default.version);
    process.exit(0);
  }

  const route = resolveCliCommand(command);
  switch (route.kind) {
    case "version": {
      const pkg = await import("../../package.json", { with: { type: "json" } });
      console.log(pkg.default.version);
      break;
    }
    case "no-db":
      await NO_DB_COMMAND_HANDLERS[route.command]();
      break;
    case "db":
      await DB_COMMAND_HANDLERS[route.command](await getDbPath());
      break;
    case "control-plane":
      await cmdControlPlane(route.command);
      break;
    case "python-only":
      console.error(`${route.command} is only supported by the Python package, not the npm CLI.\n`);
      console.log(HELP);
      process.exit(1);
    case "unknown":
      console.error(`Unknown command: ${route.command}\n`);
      console.log(HELP);
      process.exit(1);
  }
}

main().catch((err) => {
  console.error(formatFatalCliError(err));
  process.exit(1);
});
