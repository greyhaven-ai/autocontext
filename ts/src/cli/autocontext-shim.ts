#!/usr/bin/env node
/**
 * Redirect shim: `autocontext` → `autoctx` (AC-395).
 *
 * If someone runs `autocontext` after installing `autoctx`, this shim prints
 * a naming callout to stderr then delegates to the real `autoctx` CLI with
 * all original arguments.
 *
 * This is installed as an additional bin entry in package.json so that
 * `npm install -g autoctx` can expose both command names locally.
 */

import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, extname, join, resolve } from "node:path";

export function resolveRealCliPath(currentFile: string): string {
  const ext = extname(currentFile) === ".ts" ? ".ts" : ".js";
  return join(dirname(currentFile), `index${ext}`);
}

export function namingCallout(): string {
  return (
    "Note: The supported npm package and CLI are `autoctx`.\n" +
    "`autocontext` on npm is a different package.\n" +
    "Install: npm install -g autoctx\n" +
    "Forwarding to autoctx...\n"
  );
}

function childExecArgvFor(realCli: string): string[] {
  return extname(realCli) === ".ts" ? process.execArgv : [];
}

function isDirectExecution(metaUrl: string, argvPath = process.argv[1]): boolean {
  if (!argvPath) return false;
  return resolve(fileURLToPath(metaUrl)) === resolve(argvPath);
}

export function main(currentFile = fileURLToPath(import.meta.url), args = process.argv.slice(2)): void {
  const realCli = resolveRealCliPath(currentFile);
  console.error(namingCallout());

  try {
    execFileSync(process.execPath, [...childExecArgvFor(realCli), realCli, ...args], {
      stdio: ["inherit", "inherit", "inherit"],
      env: process.env,
    });
  } catch (err: unknown) {
    const code = (err as { status?: number }).status;
    process.exit(code ?? 1);
  }
}

if (isDirectExecution(import.meta.url)) {
  main();
}
