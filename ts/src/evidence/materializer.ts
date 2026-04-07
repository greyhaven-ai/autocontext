/** Evidence workspace materializer (AC-504). */

import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { createHash } from "node:crypto";
import { join, relative, resolve } from "node:path";
import type { EvidenceArtifact, EvidenceWorkspace } from "./workspace.js";

export const ARTIFACT_PRIORITY: EvidenceArtifact["kind"][] = [
  "gate_decision",
  "trace",
  "report",
  "role_output",
  "tool",
  "log",
];

const DEFAULT_BUDGET = 10 * 1024 * 1024;
const MANIFEST_FILENAME = "manifest.json";
const ACCESS_LOG_FILENAME = "evidence_access_log.json";

export interface MaterializeOptions {
  knowledgeRoot: string;
  runsRoot: string;
  sourceRunIds: string[];
  workspaceDir: string;
  budgetBytes?: number;
  scenarioName?: string;
}

export function materializeWorkspace(
  opts: MaterializeOptions,
): EvidenceWorkspace {
  const { knowledgeRoot, runsRoot, sourceRunIds, workspaceDir, scenarioName } =
    opts;
  const budgetBytes = opts.budgetBytes ?? DEFAULT_BUDGET;

  mkdirSync(workspaceDir, { recursive: true });
  cleanupPreviousWorkspace(workspaceDir);

  let allArtifacts: EvidenceArtifact[] = [];

  for (const runId of sourceRunIds) {
    const runDir = join(runsRoot, runId);
    if (existsSync(runDir)) {
      allArtifacts.push(...scanRunArtifacts(runDir, runId));
    }
  }

  if (scenarioName) {
    const kDir = join(knowledgeRoot, scenarioName);
    if (existsSync(kDir)) {
      allArtifacts.push(...scanKnowledgeArtifacts(knowledgeRoot, scenarioName));
    }
  }

  const priorityMap = new Map(ARTIFACT_PRIORITY.map((k, i) => [k, i]));
  allArtifacts.sort(
    (a, b) => (priorityMap.get(a.kind) ?? 99) - (priorityMap.get(b.kind) ?? 99),
  );

  const selected: EvidenceArtifact[] = [];
  let totalSize = 0;

  for (const artifact of allArtifacts) {
    if (totalSize + artifact.sizeBytes > budgetBytes) continue;
    if (!existsSync(artifact.path)) continue;

    const destName = `${artifact.artifactId}_${artifact.path.split("/").pop() ?? "file"}`;
    const destPath = join(workspaceDir, destName);
    try {
      copyFileSync(artifact.path, destPath);
    } catch {
      continue;
    }

    selected.push({ ...artifact, path: destName });
    totalSize += artifact.sizeBytes;
  }

  const workspace: EvidenceWorkspace = {
    workspaceDir,
    sourceRuns: sourceRunIds,
    artifacts: selected,
    totalSizeBytes: totalSize,
    materializedAt: new Date().toISOString(),
    accessedArtifacts: [],
  };

  writeFileSync(
    join(workspaceDir, MANIFEST_FILENAME),
    JSON.stringify(workspace, null, 2),
    "utf-8",
  );

  return workspace;
}

function cleanupPreviousWorkspace(workspaceDir: string): void {
  const manifestPath = join(workspaceDir, MANIFEST_FILENAME);
  if (existsSync(manifestPath)) {
    try {
      const data = JSON.parse(readFileSync(manifestPath, "utf-8")) as {
        artifacts?: Array<{ path?: unknown }>;
      };
      for (const artifact of data.artifacts ?? []) {
        if (typeof artifact.path !== "string") continue;
        const artifactPath = resolveWorkspacePath(workspaceDir, artifact.path);
        if (!artifactPath) continue;
        rmSync(artifactPath, { force: true });
      }
    } catch {
      /* skip */
    }
  }

  rmSync(join(workspaceDir, MANIFEST_FILENAME), { force: true });
  rmSync(join(workspaceDir, ACCESS_LOG_FILENAME), { force: true });
}

function resolveWorkspacePath(
  workspaceDir: string,
  relativePath: string,
): string | null {
  const root = resolve(workspaceDir);
  const candidate = resolve(root, relativePath);
  const rel = relative(root, candidate);
  if (rel === "" || rel.startsWith("..")) return null;
  return candidate;
}

export function scanRunArtifacts(
  runDir: string,
  runId: string,
): EvidenceArtifact[] {
  const artifacts: EvidenceArtifact[] = [];
  try {
    const walk = (dir: string): void => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = join(dir, entry.name);
        if (entry.isDirectory()) {
          walk(full);
          continue;
        }
        const kind = classifyFile(entry.name, relative(runDir, full));
        if (!kind) continue;
        const gen =
          extractGeneration(entry.name) ??
          extractGeneration(relative(runDir, full));
        artifacts.push({
          artifactId: makeId(runId, full),
          sourceRunId: runId,
          kind,
          path: full,
          summary: `${kind}: ${entry.name} from ${runId}`,
          sizeBytes: statSync(full).size,
          generation: gen,
        });
      }
    };
    walk(runDir);
  } catch {
    /* skip */
  }
  return artifacts;
}

export function scanKnowledgeArtifacts(
  knowledgeRoot: string,
  scenarioName: string,
): EvidenceArtifact[] {
  const kDir = join(knowledgeRoot, scenarioName);
  const sourceId = `knowledge:${scenarioName}`;
  const artifacts: EvidenceArtifact[] = [];

  const knownFiles: Record<string, EvidenceArtifact["kind"]> = {
    "playbook.md": "report",
    "dead_ends.md": "report",
  };

  for (const [fname, kind] of Object.entries(knownFiles)) {
    const fpath = join(kDir, fname);
    if (existsSync(fpath)) {
      artifacts.push({
        artifactId: makeId(sourceId, fpath),
        sourceRunId: sourceId,
        kind,
        path: fpath,
        summary: `${kind}: ${fname} for ${scenarioName}`,
        sizeBytes: statSync(fpath).size,
        generation: null,
      });
    }
  }

  const toolsDir = join(kDir, "tools");
  if (existsSync(toolsDir)) {
    for (const entry of readdirSync(toolsDir, { withFileTypes: true })) {
      if (entry.isFile() && entry.name.endsWith(".py")) {
        const full = join(toolsDir, entry.name);
        artifacts.push({
          artifactId: makeId(sourceId, full),
          sourceRunId: sourceId,
          kind: "tool",
          path: full,
          summary: `tool: ${entry.name} for ${scenarioName}`,
          sizeBytes: statSync(full).size,
          generation: null,
        });
      }
    }
  }

  const analysisDir = join(kDir, "analysis");
  if (existsSync(analysisDir)) {
    for (const entry of readdirSync(analysisDir, { withFileTypes: true })) {
      if (entry.isFile() && /^gen[_-]?\d+\.md$/i.test(entry.name)) {
        const full = join(analysisDir, entry.name);
        artifacts.push({
          artifactId: makeId(sourceId, full),
          sourceRunId: sourceId,
          kind: "report",
          path: full,
          summary: `analysis: ${entry.name} for ${scenarioName}`,
          sizeBytes: statSync(full).size,
          generation: extractGeneration(entry.name),
        });
      }
    }
  }

  return artifacts;
}

function classifyFile(
  name: string,
  relPath: string,
): EvidenceArtifact["kind"] | null {
  const lower = name.toLowerCase();
  if (
    lower.includes("gate_decision") ||
    (lower.includes("gate") && lower.endsWith(".json"))
  )
    return "gate_decision";
  if (
    lower.endsWith(".ndjson") ||
    lower.includes("event") ||
    lower.includes("trace")
  )
    return "trace";
  if (
    ["playbook", "dead_end", "report", "weakness", "session"].some((k) =>
      lower.includes(k),
    )
  )
    return "report";
  if (
    ["analyst", "coach", "architect", "competitor"].some((k) =>
      lower.includes(k),
    ) &&
    lower.includes("output")
  )
    return "role_output";
  if (relPath.includes("tools/") && lower.endsWith(".py")) return "tool";
  if (lower.endsWith(".log") || lower.includes("execution_log")) return "log";
  return null;
}

function extractGeneration(str: string): number | null {
  const m = str.match(/gen[_-]?(\d+)/i);
  return m ? parseInt(m[1], 10) : null;
}

function makeId(source: string, path: string): string {
  return createHash("sha256")
    .update(`${source}:${path}`)
    .digest("hex")
    .slice(0, 12);
}
