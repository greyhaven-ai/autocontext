import { existsSync, readdirSync, readFileSync } from "node:fs";
import { isAbsolute, join, relative, resolve } from "node:path";
import { assertSafeScenarioId } from "../knowledge/scenario-id.js";
import type { RunRow, SQLiteStore, TrajectoryRow } from "../storage/index.js";

export function buildWriteup(store: SQLiteStore, run: RunRow, knowledgeRoot: string): string {
  const persisted = latestPersistedTraceWriteup(knowledgeRoot, run.run_id);
  if (persisted !== null) {
    return persisted;
  }

  const trajectory = store.getScoreTrajectory(run.run_id);
  const sections = [
    `# Run Summary: ${run.run_id}`,
    "",
    `- **Scenario**: ${run.scenario}`,
    `- **Target generations**: ${run.target_generations}`,
    `- **Status**: ${run.status}`,
    `- **Created**: ${run.created_at}`,
    "",
    "## Score Trajectory",
    "",
  ];

  if (trajectory.length === 0) {
    sections.push("No completed generations.", "");
  } else {
    sections.push("| Gen | Best Score | Elo | Delta | Gate |");
    sections.push("|-----|------------|-----|-------|------|");
    for (const generation of trajectory) {
      sections.push(
        `| ${generation.generation_index} | ${generation.best_score.toFixed(2)} `
          + `| ${generation.elo.toFixed(0)} | ${formatDelta(generation.delta)} `
          + `| ${generation.gate_decision} |`,
      );
    }
    sections.push("");
  }

  sections.push("## Gate Decisions", "");
  if (trajectory.length === 0) {
    sections.push("No gate decisions recorded.", "");
  } else {
    for (const generation of trajectory) {
      sections.push(`- Generation ${generation.generation_index}: **${generation.gate_decision}**`);
    }
    sections.push("");
  }

  const bestOutput = bestCompetitorOutput(store, run.run_id, trajectory);
  if (bestOutput) {
    sections.push("## Best Strategy", "");
    sections.push(`Generation ${bestOutput.generation} (score: ${bestOutput.bestScore.toFixed(2)}):`, "");
    sections.push("```");
    sections.push(truncate(bestOutput.content, 500));
    sections.push("```", "");
  }

  const playbook = readScenarioPlaybook(knowledgeRoot, run.scenario);
  if (playbook !== null && !playbook.includes("No playbook yet")) {
    sections.push("## Playbook", "");
    sections.push(truncate(playbook, 1000), "");
  }

  return sections.join("\n");
}

function bestCompetitorOutput(
  store: SQLiteStore,
  runId: string,
  trajectory: TrajectoryRow[],
): { generation: number; bestScore: number; content: string } | null {
  if (trajectory.length === 0) {
    return null;
  }
  const best = trajectory.reduce((current, candidate) => (
    candidate.best_score > current.best_score ? candidate : current
  ));
  const output = store
    .getAgentOutputs(runId, best.generation_index)
    .filter((entry) => entry.role === "competitor")
    .at(-1);
  if (!output) {
    return null;
  }
  return {
    generation: best.generation_index,
    bestScore: best.best_score,
    content: output.content,
  };
}

function latestPersistedTraceWriteup(knowledgeRoot: string, runId: string): string | null {
  const writeupsDir = join(knowledgeRoot, "analytics", "writeups");
  if (!existsSync(writeupsDir)) {
    return null;
  }

  let best: { createdAt: string; markdown: string } | null = null;
  for (const file of readdirSync(writeupsDir)) {
    if (!file.endsWith(".json")) {
      continue;
    }
    const parsed = readJsonRecord(join(writeupsDir, file));
    if (parsed === null || readString(parsed, "run_id") !== runId) {
      continue;
    }
    const createdAt = readString(parsed, "created_at") ?? "";
    const markdown = renderTraceWriteup(parsed);
    if (best === null || createdAt > best.createdAt) {
      best = { createdAt, markdown };
    }
  }
  return best?.markdown ?? null;
}

function renderTraceWriteup(writeup: Record<string, unknown>): string {
  const runId = readString(writeup, "run_id") ?? "unknown";
  const metadata = readRecord(writeup, "metadata") ?? {};
  const scenario = readString(metadata, "scenario") ?? "";
  const family = readString(metadata, "scenario_family") ?? "";
  const lines = [`# Run Summary: ${runId}`, ""];
  const context = [scenario, family].filter((value) => value.length > 0).join(" | ");
  if (context.length > 0) {
    lines.push(`**Context:** ${context}`, "");
  }

  lines.push("## Trace Summary", readString(writeup, "summary") ?? "", "");
  lines.push("## Findings");
  const findings = readRecordArray(writeup, "findings");
  if (findings.length === 0) {
    lines.push("No notable findings.");
  } else {
    for (const finding of findings) {
      const evidence = readStringArray(finding, "evidence_event_ids").join(", ") || "none";
      lines.push(
        `- **${readString(finding, "title") ?? "Finding"}** `
          + `[${readString(finding, "finding_type") ?? "unknown"}/`
          + `${readString(finding, "severity") ?? "unknown"}] `
          + `${readString(finding, "description") ?? ""} (evidence: ${evidence})`,
      );
    }
  }
  lines.push("");

  lines.push("## Failure Motifs");
  const motifs = readRecordArray(writeup, "failure_motifs");
  if (motifs.length === 0) {
    lines.push("No recurring failure motifs.");
  } else {
    for (const motif of motifs) {
      lines.push(
        `- **${readString(motif, "pattern_name") ?? "motif"}**: `
          + `${readNumber(motif, "occurrence_count") ?? 0} occurrence(s)`,
      );
    }
  }
  lines.push("");

  lines.push("## Recovery Paths");
  const recoveries = readRecordArray(writeup, "recovery_paths");
  if (recoveries.length === 0) {
    lines.push("No recovery paths observed.");
  } else {
    for (const recovery of recoveries) {
      lines.push(
        `- ${readString(recovery, "failure_event_id") ?? "unknown"} -> `
          + `${readString(recovery, "recovery_event_id") ?? "unknown"} `
          + `(${readStringArray(recovery, "path_event_ids").length} events)`,
      );
    }
  }

  return lines.join("\n");
}

function readScenarioPlaybook(knowledgeRoot: string, scenario: string): string | null {
  try {
    const scenarioId = assertSafeScenarioId(scenario, "scenario");
    const playbookPath = resolveContainedPath(knowledgeRoot, scenarioId, "playbook.md");
    return existsSync(playbookPath) ? readFileSync(playbookPath, "utf-8") : null;
  } catch {
    return null;
  }
}

function readJsonRecord(path: string): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(readFileSync(path, "utf-8"));
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function readString(record: Record<string, unknown>, key: string): string | null {
  const value = record[key];
  return typeof value === "string" ? value : null;
}

function readNumber(record: Record<string, unknown>, key: string): number | null {
  const value = record[key];
  return typeof value === "number" ? value : null;
}

function readRecord(record: Record<string, unknown>, key: string): Record<string, unknown> | null {
  const value = record[key];
  return isRecord(value) ? value : null;
}

function readRecordArray(record: Record<string, unknown>, key: string): Array<Record<string, unknown>> {
  const value = record[key];
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

function readStringArray(record: Record<string, unknown>, key: string): string[] {
  const value = record[key];
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function formatDelta(value: number): string {
  return value >= 0 ? `+${value.toFixed(4)}` : value.toFixed(4);
}

function truncate(value: string, maxLength: number): string {
  return value.length > maxLength ? `${value.slice(0, maxLength)}...` : value;
}

function resolveContainedPath(root: string, ...segments: string[]): string {
  const resolvedRoot = resolve(root);
  const target = resolve(resolvedRoot, ...segments);
  const pathToTarget = relative(resolvedRoot, target);
  if (pathToTarget === "" || (!pathToTarget.startsWith("..") && !isAbsolute(pathToTarget))) {
    return target;
  }
  throw new Error("path escapes configured root");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
