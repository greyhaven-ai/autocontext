import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import {
  buildTraceGateOperatorView,
  type TraceGateOperatorView,
} from "../analytics/trace-gate-operator-view.js";
import { TraceFindingReportSchema, type TraceFindingReport } from "../analytics/trace-findings.js";
import type { HarnessChangeProposal } from "../control-plane/contract/types.js";
import { validateHarnessChangeProposal } from "../control-plane/contract/validators.js";
import type { CockpitApiResponse } from "./cockpit-api.js";

export interface TraceGateReviewApiRoutes {
  getByRunId(runId: string): CockpitApiResponse;
}

export interface BuildTraceGateReviewApiRoutesOptions {
  runsRoot: string;
  loadReport?: (runId: string) => TraceFindingReport | null;
  loadProposals?: (runId: string) => readonly HarnessChangeProposal[];
}

export function buildTraceGateReviewApiRoutes(
  opts: BuildTraceGateReviewApiRoutesOptions,
): TraceGateReviewApiRoutes {
  return {
    getByRunId(runId) {
      let cleanRunId: string;
      try {
        cleanRunId = validateRunId(opts.runsRoot, runId);
      } catch (error) {
        return { status: 422, body: { detail: errorMessage(error) } };
      }

      try {
        const view = buildTraceGateOperatorView({
          run_id: cleanRunId,
          report: opts.loadReport
            ? opts.loadReport(cleanRunId)
            : loadLatestTraceFindingReport(opts.runsRoot, cleanRunId),
          proposals: opts.loadProposals
            ? opts.loadProposals(cleanRunId)
            : loadHarnessChangeProposals(opts.runsRoot, cleanRunId),
        });
        return { status: 200, body: view };
      } catch (error) {
        return { status: 500, body: { detail: errorMessage(error) } };
      }
    },
  };
}

export function loadLatestTraceFindingReport(
  runsRoot: string,
  runId: string,
): TraceFindingReport | null {
  const runRoot = resolveRunRoot(runsRoot, runId);
  const candidates = [
    ...reportFilesInDir(join(runRoot, "trace-findings")),
    ...reportFilesInDir(join(runRoot, "trace_findings")),
    join(runRoot, "trace-finding-report.json"),
    join(runRoot, "trace_finding_report.json"),
  ]
    .filter((path) => existsSync(path))
    .sort((left, right) => statSync(right).mtimeMs - statSync(left).mtimeMs);
  const first = candidates[0];
  return first ? TraceFindingReportSchema.parse(readJson(first)) : null;
}

export function loadHarnessChangeProposals(
  runsRoot: string,
  runId: string,
): HarnessChangeProposal[] {
  const runRoot = resolveRunRoot(runsRoot, runId);
  return [join(runRoot, "harness-proposals"), join(runRoot, "harness_proposals")]
    .flatMap(jsonFilesInDir)
    .map((path) => readHarnessChangeProposal(path));
}

function validateRunId(runsRoot: string, runId: string): string {
  const cleanRunId = runId.trim();
  resolveRunRoot(runsRoot, cleanRunId);
  return cleanRunId;
}

function resolveRunRoot(runsRoot: string, runId: string): string {
  const cleanRunId = runId.trim();
  if (!cleanRunId) {
    throw new Error("run_id is required");
  }
  const root = resolve(runsRoot);
  const candidate = resolve(root, cleanRunId);
  if (candidate === root || !candidate.startsWith(`${root}/`)) {
    throw new Error(`run_id escapes runs root: '${runId}'`);
  }
  return candidate;
}

function readHarnessChangeProposal(path: string): HarnessChangeProposal {
  const payload = readJson(path);
  const validation = validateHarnessChangeProposal(payload);
  if (!validation.valid) {
    throw new Error(`Invalid HarnessChangeProposal at ${path}: ${validation.errors.join("; ")}`);
  }
  return payload as HarnessChangeProposal;
}

function reportFilesInDir(dir: string): string[] {
  return jsonFilesInDir(dir).filter((path) => !path.endsWith(".tmp"));
}

function jsonFilesInDir(dir: string): string[] {
  if (!existsSync(dir)) {
    return [];
  }
  return readdirSync(dir)
    .filter((entry) => entry.endsWith(".json"))
    .map((entry) => join(dir, entry));
}

function readJson(path: string): unknown {
  return JSON.parse(readFileSync(path, "utf-8"));
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export type { TraceGateOperatorView };
