import { existsSync, readdirSync, readFileSync } from "node:fs";
import { isAbsolute, join, relative, resolve } from "node:path";

import type { ContextSelectionDecisionInput } from "./context-selection-report.js";

const SCHEMA_VERSION = 1;
const SAFE_STAGE_RE = /^[A-Za-z0-9_.-]+$/;
const DECISION_FILE_RE = /^gen_(?<generation>[0-9]+)_(?<stage>[A-Za-z0-9_.-]+)\.json$/;

export function loadContextSelectionDecisions(
  runsRoot: string,
  runId: string,
): ContextSelectionDecisionInput[] {
  const cleanRunId = runId.trim();
  const contextDir = join(resolveRunRoot(runsRoot, cleanRunId), "context_selection");
  if (!existsSync(contextDir)) {
    return [];
  }
  const decisions: ContextSelectionDecisionInput[] = [];
  for (const fileName of readdirSync(contextDir).sort()) {
    const match = DECISION_FILE_RE.exec(fileName);
    if (!match?.groups) continue;
    const data = JSON.parse(readFileSync(join(contextDir, fileName), "utf-8"));
    const decision = decisionFromPayload(data, {
      runId: cleanRunId,
      generation: Number.parseInt(match.groups.generation!, 10),
      stage: match.groups.stage!,
    });
    if (decision) {
      decisions.push(decision);
    }
  }
  return decisions.sort((a, b) =>
    coerceNumber(a.generation) - coerceNumber(b.generation) ||
    String(a.stage ?? "").localeCompare(String(b.stage ?? "")));
}

function resolveRunRoot(runsRoot: string, runId: string): string {
  const normalized = runId.trim();
  if (!normalized) {
    throw new Error("run_id is required");
  }
  const root = resolve(runsRoot);
  const candidate = resolve(root, normalized);
  if (candidate === root) {
    throw new Error(`run_id must name a run subdirectory: ${runId}`);
  }
  const relativePath = relative(root, candidate);
  if (relativePath === "" || relativePath.startsWith("..") || isAbsolute(relativePath)) {
    throw new Error(`run_id escapes runs root: ${runId}`);
  }
  return candidate;
}

function decisionFromPayload(
  data: unknown,
  expected: { runId: string; generation: number; stage: string },
): ContextSelectionDecisionInput | null {
  if (!isRecord(data)) return null;
  if (data.schema_version !== SCHEMA_VERSION) return null;
  if (data.run_id !== expected.runId) return null;
  if (!Number.isInteger(data.generation) || data.generation !== expected.generation) return null;
  if (data.stage !== expected.stage || !SAFE_STAGE_RE.test(expected.stage)) return null;
  if (typeof data.scenario_name !== "string") return null;
  if (!Array.isArray(data.candidates)) return null;
  if (!hasDecisionMetrics(data.metrics)) return null;
  return data as ContextSelectionDecisionInput;
}

function hasDecisionMetrics(value: unknown): boolean {
  if (!isRecord(value)) return false;
  return [
    "candidate_count",
    "selected_count",
    "candidate_token_estimate",
    "selected_token_estimate",
  ].every((key) => Object.prototype.hasOwnProperty.call(value, key));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function coerceNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}
