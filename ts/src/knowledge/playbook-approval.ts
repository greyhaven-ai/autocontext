import { existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { RunId, ScenarioName } from "../domain/ids.js";
import { resolveScenarioRoot } from "./scenario-paths.js";

export interface PendingPlaybookProvenance {
  schema_version: 1;
  scenario_name: string;
  source_run_id: string;
  generation: number;
  curator_decision: string;
  created_at: string;
  status: "pending";
}

export interface PendingPlaybookView {
  hasPending: boolean;
  content: string;
  diff: string;
  provenance: PendingPlaybookProvenance | null;
}

export interface StagePendingPlaybookOptions {
  sourceRunId: RunId;
  generation: number;
  curatorDecision: string;
  createdAt?: string;
}

export function stagePendingPlaybook(
  knowledgeRoot: string,
  scenarioName: ScenarioName,
  content: string,
  opts: StagePendingPlaybookOptions,
): "pending" {
  const scenarioDir = resolveScenarioRoot(knowledgeRoot, scenarioName);
  mkdirSync(scenarioDir, { recursive: true });
  if (existsSync(pendingMd(scenarioDir)) || existsSync(pendingJson(scenarioDir))) {
    throw new Error("pending playbook already exists; approve or reject it before staging another");
  }
  writeFileSync(pendingMd(scenarioDir), `${content.trim()}\n`, "utf-8");
  writeFileSync(
    pendingJson(scenarioDir),
    JSON.stringify(
      {
        schema_version: 1,
        scenario_name: scenarioName,
        source_run_id: opts.sourceRunId,
        generation: opts.generation,
        curator_decision: opts.curatorDecision,
        created_at: opts.createdAt ?? new Date().toISOString(),
        status: "pending",
      } satisfies PendingPlaybookProvenance,
      null,
      2,
    ),
    "utf-8",
  );
  return "pending";
}

export function readPendingPlaybook(
  knowledgeRoot: string,
  scenarioName: ScenarioName,
): PendingPlaybookView {
  const scenarioDir = resolveScenarioRoot(knowledgeRoot, scenarioName);
  const pendingPath = pendingMd(scenarioDir);
  const provenancePath = pendingJson(scenarioDir);
  if (!existsSync(pendingPath) || !existsSync(provenancePath)) {
    return { hasPending: false, content: "", diff: "", provenance: null };
  }
  const content = readFileSync(pendingPath, "utf-8");
  const livePath = join(scenarioDir, "playbook.md");
  const live = existsSync(livePath) ? readFileSync(livePath, "utf-8") : "";
  return {
    hasPending: true,
    content,
    diff: simpleLineDiff(live, content),
    provenance: JSON.parse(readFileSync(provenancePath, "utf-8")) as PendingPlaybookProvenance,
  };
}

export function approvePendingPlaybook(
  knowledgeRoot: string,
  scenarioName: ScenarioName,
  writeLivePlaybook: (scenarioName: ScenarioName, content: string) => void,
): { ok: boolean; status: "approved" | "missing" } {
  const pending = readPendingPlaybook(knowledgeRoot, scenarioName);
  if (!pending.hasPending || pending.provenance === null) return { ok: false, status: "missing" };
  writeLivePlaybook(scenarioName, pending.content);
  clearPending(resolveScenarioRoot(knowledgeRoot, scenarioName));
  return { ok: true, status: "approved" };
}

export function rejectPendingPlaybook(
  knowledgeRoot: string,
  scenarioName: ScenarioName,
): { ok: boolean; status: "rejected" | "missing" } {
  const pending = readPendingPlaybook(knowledgeRoot, scenarioName);
  if (!pending.hasPending || pending.provenance === null) return { ok: false, status: "missing" };
  clearPending(resolveScenarioRoot(knowledgeRoot, scenarioName));
  return { ok: true, status: "rejected" };
}

function clearPending(scenarioDir: string): void {
  for (const path of [pendingMd(scenarioDir), pendingJson(scenarioDir)]) {
    if (existsSync(path)) unlinkSync(path);
  }
}

function pendingMd(scenarioDir: string): string {
  return join(scenarioDir, "playbook.pending.md");
}

function pendingJson(scenarioDir: string): string {
  return join(scenarioDir, "playbook.pending.json");
}

function simpleLineDiff(before: string, after: string): string {
  return [
    "--- playbook.md",
    "+++ playbook.pending.md",
    ...before
      .split(/\r?\n/)
      .filter(Boolean)
      .map((line) => `-${line}`),
    ...after
      .split(/\r?\n/)
      .filter(Boolean)
      .map((line) => `+${line}`),
  ].join("\n");
}
