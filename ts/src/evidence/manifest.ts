/** Evidence workspace prompt rendering (AC-504). */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { EvidenceArtifact, EvidenceWorkspace } from "./workspace.js";

const KIND_LABELS: Record<string, string> = {
  gate_decision: "Gate decisions (advance/retry/rollback with deltas)",
  trace: "Traces (run event streams)",
  report: "Reports (session + weakness reports)",
  role_output: "Role outputs (analyst, architect, coach)",
  tool: "Tools (architect-generated)",
  log: "Logs (execution logs)",
};

export function renderEvidenceManifest(workspace: EvidenceWorkspace): string {
  const n = workspace.artifacts.length;
  const runs = workspace.sourceRuns.length;
  const sizeMb =
    Math.round((workspace.totalSizeBytes / (1024 * 1024)) * 10) / 10;

  const lines = [
    "## Prior-Run Evidence",
    `Available: ${n} artifacts from ${runs} prior run(s) (${sizeMb} MB)`,
  ];

  const kindCounts = new Map<string, number>();
  for (const a of workspace.artifacts) {
    kindCounts.set(a.kind, (kindCounts.get(a.kind) ?? 0) + 1);
  }

  for (const kind of [
    "gate_decision",
    "trace",
    "report",
    "role_output",
    "tool",
    "log",
  ]) {
    const count = kindCounts.get(kind);
    if (count && count > 0) {
      lines.push(`- ${KIND_LABELS[kind] ?? kind}: ${count}`);
    }
  }

  lines.push("");
  lines.push(
    'Reference artifacts by ID (e.g., "gate_abc123") for detailed inspection.',
  );

  return lines.join("\n");
}

export function renderArtifactDetail(
  artifact: EvidenceArtifact,
  workspaceDir: string,
): string {
  const path = join(workspaceDir, artifact.path);
  if (!existsSync(path)) {
    return `[Artifact ${artifact.artifactId} not found at ${artifact.path}]`;
  }
  try {
    const content = readFileSync(path, "utf-8");
    return `## ${artifact.kind}: ${artifact.summary}\n\n${content}`;
  } catch {
    return `[Could not read artifact ${artifact.artifactId}: binary or inaccessible]`;
  }
}
