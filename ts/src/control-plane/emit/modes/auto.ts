// auto mode — detect the best available emit mode, in order:
//   1. gh installed + authenticated  → gh mode
//   2. git installed + remote OK     → git mode
//   3. else                          → patch-only
//
// Per spec §9.6 the resolved mode is always echoed — to stderr by the CLI
// and in the JSON output — so operators can see which branch the CLI took.
// Detection is dependency-injected for testability.

import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";

export type ResolvedMode = "gh" | "git" | "patch-only";

export interface AutoDetector {
  gh(): boolean;
  git(): boolean;
}

export interface ResolveAutoModeInputs {
  readonly cwd?: string;
  readonly detect?: AutoDetector;
}

export interface ResolveAutoModeResult {
  readonly mode: ResolvedMode;
  readonly reason: string;
}

export function resolveAutoMode(inputs: ResolveAutoModeInputs = {}): ResolveAutoModeResult {
  const detect = inputs.detect ?? defaultDetector(inputs.cwd ?? process.cwd());
  if (detect.gh()) {
    return { mode: "gh", reason: "gh CLI installed and authenticated — using gh mode" };
  }
  if (detect.git()) {
    return { mode: "git", reason: "git installed with remote configured — using git mode" };
  }
  return {
    mode: "patch-only",
    reason: "neither gh nor git are usable — falling back to patch-only mode",
  };
}

function defaultDetector(cwd: string): AutoDetector {
  return {
    gh(): boolean {
      try {
        execFileSync("gh", ["auth", "status"], { cwd, stdio: "ignore" });
        return true;
      } catch {
        return false;
      }
    },
    git(): boolean {
      try {
        execFileSync("git", ["--version"], { cwd, stdio: "ignore" });
      } catch {
        return false;
      }
      if (!existsSync(join(cwd, ".git"))) return false;
      // Require at least one remote for git mode to be meaningful (the
      // operator will push from the printed command).
      try {
        const out = execFileSync("git", ["remote"], {
          cwd,
          stdio: ["ignore", "pipe", "ignore"],
        });
        return out.toString("utf-8").trim().length > 0;
      } catch {
        return false;
      }
    },
  };
}
