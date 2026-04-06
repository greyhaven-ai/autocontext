/** Environment snapshot collector (AC-503). Uses execFileSync (no shell) for safety. */

import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import {
  cpus,
  freemem,
  hostname,
  platform,
  release,
  totalmem,
  userInfo,
} from "node:os";
import { join } from "node:path";
import type { EnvironmentSnapshot, PackageInfo } from "./snapshot.js";

const SUBPROCESS_TIMEOUT = 500;
const MAX_NOTABLE_FILES = 50;

const KNOWN_LOCKFILES = [
  "poetry.lock",
  "Pipfile.lock",
  "uv.lock",
  "pdm.lock",
  "conda-lock.yml",
  "package-lock.json",
  "yarn.lock",
  "pnpm-lock.yaml",
  "bun.lock",
  "Gemfile.lock",
  "Cargo.lock",
  "go.sum",
  "composer.lock",
];

const RUNTIME_CHECKS: [string, string, string[]][] = [
  ["node", "node", ["--version"]],
  ["python3", "python3", ["--version"]],
  ["go", "go", ["version"]],
  ["ruby", "ruby", ["--version"]],
  ["rustc", "rustc", ["--version"]],
  ["deno", "deno", ["--version"]],
  ["bun", "bun", ["--version"]],
];

function safeExec(cmd: string, args: string[]): string {
  try {
    return execFileSync(cmd, args, {
      timeout: SUBPROCESS_TIMEOUT,
      encoding: "utf8",
      stdio: ["pipe", "pipe", "pipe"],
    }).trim();
  } catch {
    return "";
  }
}

export function collectSnapshot(): EnvironmentSnapshot {
  const core = collectCore();
  const runtimes = collectRuntimes();
  const packages = collectPackages();
  const fs = collectFilesystem(core.workingDirectory);
  const git = collectGit();
  const system = collectSystem();

  return {
    ...core,
    ...runtimes,
    ...packages,
    ...fs,
    ...git,
    ...system,
    collectedAt: new Date().toISOString(),
    collectorVersion: "1.0.0",
    redactedFields: [],
  };
}

export function collectCore(): Pick<
  EnvironmentSnapshot,
  | "workingDirectory"
  | "osName"
  | "osVersion"
  | "shell"
  | "hostname"
  | "username"
> {
  let user = "";
  try {
    user = userInfo().username;
  } catch {
    /* fallback */
  }
  return {
    workingDirectory: process.cwd(),
    osName: platform(),
    osVersion: release(),
    shell: process.env.SHELL ?? process.env.COMSPEC ?? "",
    hostname: hostname(),
    username: user || process.env.USER || process.env.USERNAME || "",
  };
}

export function collectRuntimes(): Pick<
  EnvironmentSnapshot,
  "pythonVersion" | "availableRuntimes"
> {
  const available: Record<string, string> = {};
  for (const [name, cmd, args] of RUNTIME_CHECKS) {
    const out = safeExec(cmd, args);
    if (out) {
      const version = out.split(/\s+/).find((t) => /^\d/.test(t));
      available[name] = version ?? out.slice(0, 50);
    }
  }
  const pythonVersion = available.python3 ?? "";
  delete available.python3;
  return { pythonVersion, availableRuntimes: available };
}

export function collectPackages(): Pick<
  EnvironmentSnapshot,
  "installedPackages" | "lockfilesFound"
> {
  const packages: PackageInfo[] = [];
  const cwd = process.cwd();
  try {
    const pkgPath = join(cwd, "package.json");
    if (existsSync(pkgPath)) {
      const pkg = JSON.parse(readFileSync(pkgPath, "utf-8")) as Record<
        string,
        unknown
      >;
      for (const depKey of ["dependencies", "devDependencies"]) {
        const deps = pkg[depKey] as Record<string, string> | undefined;
        if (deps) {
          for (const [name, version] of Object.entries(deps)) {
            packages.push({ name, version: String(version) });
          }
        }
      }
    }
  } catch {
    /* skip */
  }

  const lockfiles: string[] = [];
  for (const name of KNOWN_LOCKFILES) {
    try {
      if (existsSync(join(cwd, name))) lockfiles.push(name);
    } catch {
      /* skip */
    }
  }

  return { installedPackages: packages, lockfilesFound: lockfiles };
}

export function collectFilesystem(
  cwd: string,
): Pick<EnvironmentSnapshot, "notableFiles" | "directoryCount" | "fileCount"> {
  const notable: string[] = [];
  let dirCount = 0;
  let fileCount = 0;
  try {
    const entries = readdirSync(cwd, { withFileTypes: true })
      .filter(
        (e) =>
          !e.name.startsWith(".") ||
          [".env.example", ".gitignore", ".dockerignore"].includes(e.name),
      )
      .sort((a, b) => a.name.localeCompare(b.name));
    for (const entry of entries) {
      if (entry.isDirectory()) dirCount++;
      else fileCount++;
      if (notable.length < MAX_NOTABLE_FILES) {
        notable.push(entry.isDirectory() ? `${entry.name}/` : entry.name);
      }
    }
  } catch {
    /* skip */
  }
  return { notableFiles: notable, directoryCount: dirCount, fileCount };
}

export function collectGit(): Pick<
  EnvironmentSnapshot,
  "gitBranch" | "gitCommit" | "gitDirty" | "gitWorktree"
> {
  const branch = safeExec("git", ["rev-parse", "--abbrev-ref", "HEAD"]);
  if (!branch)
    return {
      gitBranch: null,
      gitCommit: null,
      gitDirty: false,
      gitWorktree: false,
    };

  const commit = safeExec("git", ["rev-parse", "--short", "HEAD"]);
  const status = safeExec("git", ["status", "--porcelain"]);
  const commonDir = safeExec("git", ["rev-parse", "--git-common-dir"]);
  const gitDir = safeExec("git", ["rev-parse", "--git-dir"]);

  return {
    gitBranch: branch || null,
    gitCommit: commit || null,
    gitDirty: status.length > 0,
    gitWorktree: commonDir !== "" && gitDir !== "" && commonDir !== gitDir,
  };
}

export function collectSystem(): Pick<
  EnvironmentSnapshot,
  "memoryTotalMb" | "memoryAvailableMb" | "diskFreeGb" | "cpuCount"
> {
  let diskFreeGb = 0;
  const dfOut = safeExec("df", ["-k", "."]);
  if (dfOut) {
    const lines = dfOut.split("\n");
    if (lines.length >= 2) {
      const parts = lines[lines.length - 1].trim().split(/\s+/);
      const avail = parseInt(parts[3], 10);
      if (!isNaN(avail))
        diskFreeGb = Math.round((avail / (1024 * 1024)) * 10) / 10;
    }
  }

  return {
    memoryTotalMb: Math.round(totalmem() / (1024 * 1024)),
    memoryAvailableMb: Math.round(freemem() / (1024 * 1024)),
    diskFreeGb,
    cpuCount: cpus().length,
  };
}
