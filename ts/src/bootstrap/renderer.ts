/** Environment snapshot prompt rendering (AC-503). */

import type { EnvironmentSnapshot } from "./snapshot.js";

export function renderPromptSection(snapshot: EnvironmentSnapshot): string {
  const lines: string[] = ["## Environment"];

  const coreParts = [
    `Python ${snapshot.pythonVersion}`,
    `${snapshot.osName} ${snapshot.osVersion}`,
    snapshot.shell?.split("/").pop() ?? "",
    snapshot.cpuCount ? `${snapshot.cpuCount} CPU` : "",
    snapshot.memoryTotalMb ? `${snapshot.memoryTotalMb}MB RAM` : "",
    snapshot.diskFreeGb ? `${snapshot.diskFreeGb}GB free` : "",
  ].filter(Boolean);
  lines.push(coreParts.join(" | "));

  if (snapshot.gitBranch) {
    const dirty = snapshot.gitDirty ? ", dirty" : ", clean";
    const wt = snapshot.gitWorktree ? ", worktree" : "";
    const commit = snapshot.gitCommit
      ? ` (${snapshot.gitCommit}${dirty}${wt})`
      : "";
    lines.push(`Git: ${snapshot.gitBranch}${commit}`);
  }

  if (Object.keys(snapshot.availableRuntimes).length > 0) {
    const parts = Object.entries(snapshot.availableRuntimes)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([name, ver]) => `${name} ${ver}`);
    lines.push(`Runtimes: ${parts.join(", ")}`);
  }

  if (snapshot.notableFiles.length > 0) {
    const top = snapshot.notableFiles.slice(0, 8);
    const extras =
      snapshot.fileCount || snapshot.directoryCount
        ? ` (${snapshot.fileCount} files, ${snapshot.directoryCount} dirs)`
        : "";
    lines.push(`Notable: ${top.join(", ")}${extras}`);
  }

  if (snapshot.installedPackages.length > 0) {
    const n = snapshot.installedPackages.length;
    const lockNote =
      snapshot.lockfilesFound.length > 0
        ? ` (${snapshot.lockfilesFound.join(", ")})`
        : "";
    lines.push(`Packages: ${n} top-level${lockNote}`);
  }

  return lines.join("\n");
}

export function renderFullJson(snapshot: EnvironmentSnapshot): string {
  return JSON.stringify(snapshot, null, 2);
}
