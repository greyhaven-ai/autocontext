#!/usr/bin/env node
/**
 * autocontext CLI — command-line dispatcher for the evaluation harness.
 */

import { buildCliHelp, resolveCliCommand } from "./command-registry.js";
import {
  DB_COMMAND_HANDLERS,
  NO_DB_COMMAND_HANDLERS,
  cmdControlPlane,
  getDbPath,
  parseVersionOptions,
  reportFatalCliError,
} from "./command-handlers.js";

const HELP = buildCliHelp();
const FULL_HELP = buildCliHelp({ all: true });
const VERSION_HELP = `autoctx version [--json]\n\nShow package version and runtime identity.`;

async function printVersion(json: boolean): Promise<void> {
  const pkg = await import("../../package.json", { with: { type: "json" } });
  if (json) {
    console.log(JSON.stringify({ package: "autoctx", version: pkg.default.version, runtime: "typescript" }));
  } else {
    console.log(pkg.default.version);
  }
}

async function main(): Promise<void> {
  const command = process.argv[2];

  if (command === "--help" || command === "-h") {
    console.log(process.argv.slice(3).includes("--all") ? FULL_HELP : HELP);
    process.exit(0);
  }

  if (!command) {
    console.log(HELP);
    process.exit(0);
  }

  if (command === "--version") {
    const options = parseVersionOptions(process.argv.slice(3));
    if (options.help) {
      console.log(VERSION_HELP);
      process.exit(0);
    }
    await printVersion(options.json);
    process.exit(0);
  }

  const route = resolveCliCommand(command);
  switch (route.kind) {
    case "version": {
      const options = parseVersionOptions(process.argv.slice(3));
      if (options.help) {
        console.log(VERSION_HELP);
        break;
      }
      await printVersion(options.json);
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
  process.exit(reportFatalCliError(err));
});
