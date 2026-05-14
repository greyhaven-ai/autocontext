import { existsSync, readdirSync, readFileSync, realpathSync } from "node:fs";
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
  const runRoot = resolveRunRoot(runsRoot, cleanRunId);
  const contextDir = resolveContextSelectionDir(runRoot);
  if (!existsSync(contextDir)) {
    return [];
  }
  const decisions: ContextSelectionDecisionInput[] = [];
  for (const fileName of readdirSync(contextDir).sort()) {
    const match = DECISION_FILE_RE.exec(fileName);
    if (!match?.groups) continue;
    const data = JSON.parse(readFileSync(resolveContextSelectionFile(contextDir, fileName), "utf-8"));
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
  if (existsSync(candidate)) {
    const realRoot = realpathSync(root);
    const realCandidate = realpathSync(candidate);
    if (!isContainedPath(realRoot, realCandidate)) {
      throw new Error(`run_id escapes runs root: ${runId}`);
    }
    return realCandidate;
  }
  return candidate;
}

function resolveContextSelectionDir(runRoot: string): string {
  const contextDir = join(runRoot, "context_selection");
  if (!existsSync(contextDir)) {
    return contextDir;
  }
  const realRunRoot = realpathSync(runRoot);
  const realContextDir = realpathSync(contextDir);
  if (!isContainedPath(realRunRoot, realContextDir)) {
    throw new Error("context_selection directory escapes run root");
  }
  return realContextDir;
}

function isContainedPath(root: string, candidate: string): boolean {
  const relativePath = relative(root, candidate);
  return relativePath !== "" && !relativePath.startsWith("..") && !isAbsolute(relativePath);
}

function resolveContextSelectionFile(contextDir: string, fileName: string): string {
  const filePath = join(contextDir, fileName);
  const realContextDir = realpathSync(contextDir);
  const realFilePath = realpathSync(filePath);
  if (!isContainedPath(realContextDir, realFilePath)) {
    throw new Error("context_selection decision file escapes context-selection directory");
  }
  return realFilePath;
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
