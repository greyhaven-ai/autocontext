/**
 * Utility functions.
 */

import { execFileSync } from "node:child_process";

export function which(cmd: string): string | null {
  try {
    return execFileSync("which", [cmd], { encoding: "utf8" }).trim() || null;
  } catch {
    return null;
  }
}
