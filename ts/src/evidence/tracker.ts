/** Evidence access tracking (AC-504). */

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { EvidenceWorkspace } from "./workspace.js";

const ACCESS_LOG_FILENAME = "evidence_access_log.json";

export function recordAccess(
  workspace: EvidenceWorkspace,
  artifactId: string,
): void {
  if (!workspace.accessedArtifacts.includes(artifactId)) {
    workspace.accessedArtifacts.push(artifactId);
  }
}

export function saveAccessLog(workspace: EvidenceWorkspace): void {
  const logPath = join(workspace.workspaceDir, ACCESS_LOG_FILENAME);
  writeFileSync(
    logPath,
    JSON.stringify({ accessed: workspace.accessedArtifacts }, null, 2),
    "utf-8",
  );
}

export function loadAccessLog(workspaceDir: string): string[] {
  const logPath = join(workspaceDir, ACCESS_LOG_FILENAME);
  if (!existsSync(logPath)) return [];
  try {
    const data = JSON.parse(readFileSync(logPath, "utf-8")) as {
      accessed?: string[];
    };
    return data.accessed ?? [];
  } catch {
    return [];
  }
}

export function computeUtilization(workspace: EvidenceWorkspace): {
  totalArtifacts: number;
  accessedCount: number;
  utilizationPercent: number;
  byKind: Record<string, { total: number; accessed: number }>;
} {
  const total = workspace.artifacts.length;
  const accessed = workspace.accessedArtifacts.length;
  const pct = total > 0 ? Math.round((accessed / total) * 1000) / 10 : 0;

  const accessedSet = new Set(workspace.accessedArtifacts);
  const byKind: Record<string, { total: number; accessed: number }> = {};
  for (const a of workspace.artifacts) {
    if (!byKind[a.kind]) byKind[a.kind] = { total: 0, accessed: 0 };
    byKind[a.kind].total++;
    if (accessedSet.has(a.artifactId)) byKind[a.kind].accessed++;
  }

  return {
    totalArtifacts: total,
    accessedCount: accessed,
    utilizationPercent: pct,
    byKind,
  };
}
