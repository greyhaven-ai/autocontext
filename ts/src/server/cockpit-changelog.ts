import type { AgentOutputRow, SQLiteStore } from "../storage/index.js";

export function buildChangelog(store: SQLiteStore, runId: string): Record<string, unknown> {
  const generations = store.getGenerations(runId);
  if (generations.length === 0) {
    return { run_id: runId, generations: [] };
  }

  const outputsByGeneration = new Map<number, AgentOutputRow[]>();
  for (const generation of generations) {
    outputsByGeneration.set(
      generation.generation_index,
      store.getAgentOutputs(runId, generation.generation_index),
    );
  }

  const entries = generations.map((generation, index) => {
    const previous = index === 0 ? null : generations[index - 1]!;
    const previousBestScore = previous?.best_score ?? 0;
    const previousElo = previous?.elo ?? 1000;
    const outputs = outputsByGeneration.get(generation.generation_index) ?? [];
    return {
      generation: generation.generation_index,
      score_delta: roundDelta(generation.best_score - previousBestScore),
      elo_delta: roundDelta(generation.elo - previousElo),
      gate_decision: generation.gate_decision,
      new_tools: outputs
        .filter((output) => output.role === "architect")
        .flatMap((output) => extractToolNames(output.content)),
      playbook_changed: outputs
        .filter((output) => output.role === "coach")
        .some((output) => extractMarkedSection(output.content, "PLAYBOOK").trim().length > 0),
      duration_seconds: generation.duration_seconds,
    };
  });
  return { run_id: runId, generations: entries };
}

function extractToolNames(content: string): string[] {
  return jsonCandidates(content)
    .flatMap((candidate) => {
      const parsed = parseJson(candidate);
      if (Array.isArray(parsed)) {
        return parsed
          .filter(isRecord)
          .map((entry) => readString(entry, "name"))
          .filter((name): name is string => name !== null);
      }
      if (isRecord(parsed) && Array.isArray(parsed.tools)) {
        return parsed.tools
          .filter(isRecord)
          .map((entry) => readString(entry, "name"))
          .filter((name): name is string => name !== null);
      }
      return [];
    });
}

function jsonCandidates(content: string): string[] {
  const candidates = [content];
  const fencedJson = /```(?:json)?\s*([\s\S]*?)```/gi;
  let match: RegExpExecArray | null;
  while ((match = fencedJson.exec(content)) !== null) {
    const candidate = match[1]?.trim();
    if (candidate) {
      candidates.push(candidate);
    }
  }
  return candidates;
}

function parseJson(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function extractMarkedSection(content: string, name: string): string {
  const marker = escapeRegExp(name);
  const match = new RegExp(
    `<!--\\s*${marker}_START\\s*-->([\\s\\S]*?)<!--\\s*${marker}_END\\s*-->`,
    "i",
  ).exec(content);
  return match?.[1] ?? "";
}

function roundDelta(value: number): number {
  return Math.round(value * 1_000_000) / 1_000_000;
}

function readString(record: Record<string, unknown>, key: string): string | null {
  const value = record[key];
  return typeof value === "string" ? value : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
