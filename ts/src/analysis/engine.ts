/**
 * Analysis engine — first-class `analyze` surface (AC-448).
 *
 * Interprets completed runs, missions, simulations, and investigations.
 * Produces structured explanations with attribution, regressions,
 * confidence, and limitations.
 *
 * Two modes:
 * - Single-target: analyze one artifact (explain what happened)
 * - Compare: diff two artifacts (explain what changed and why)
 *
 * Built on top of existing analytics modules (credit-assignment,
 * rubric-drift, run-trace) and artifact persistence from simulate/investigate.
 */

import { existsSync, readFileSync, writeFileSync, mkdirSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { SQLiteStore } from "../storage/index.js";
import { MissionManager } from "../mission/manager.js";

interface AnalysisEngineOpts {
  knowledgeRoot: string;
  runsRoot?: string;
  dbPath?: string;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type AnalysisTargetType = "run" | "simulation" | "investigation" | "mission";

export interface AnalysisTarget {
  id: string;
  type: AnalysisTargetType;
}

export interface AnalysisRequest {
  id: string;
  type: AnalysisTargetType;
  focus?: string;
}

export interface CompareRequest {
  left: AnalysisTarget;
  right: AnalysisTarget;
  focus?: string;
}

export interface Finding {
  kind: "driver" | "regression" | "improvement" | "observation" | "conclusion" | "warning";
  statement: string;
  evidence: string[];
}

export interface Attribution {
  topFactors: Array<{ name: string; weight: number }>;
}

export interface AnalysisSummary {
  headline: string;
  confidence: number;
}

export interface AnalysisResult {
  id: string;
  target: AnalysisTarget;
  compareTarget?: AnalysisTarget;
  mode: "single" | "compare";
  summary: AnalysisSummary;
  findings: Finding[];
  regressions: string[];
  attribution?: Attribution;
  limitations: string[];
  artifacts: {
    reportPath?: string;
  };
}

// ---------------------------------------------------------------------------
// Engine
// ---------------------------------------------------------------------------

function generateId(): string {
  return `analysis_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

export class AnalysisEngine {
  private knowledgeRoot: string;
  private runsRoot: string;
  private dbPath?: string;

  constructor(opts: string | AnalysisEngineOpts) {
    if (typeof opts === "string") {
      this.knowledgeRoot = opts;
      this.runsRoot = opts;
      this.dbPath = undefined;
      return;
    }

    this.knowledgeRoot = opts.knowledgeRoot;
    this.runsRoot = opts.runsRoot ?? opts.knowledgeRoot;
    this.dbPath = opts.dbPath;
  }

  /**
   * Analyze a single artifact.
   */
  analyze(request: AnalysisRequest): AnalysisResult {
    const id = generateId();
    const artifact = this.loadArtifact(request.id, request.type);

    if (!artifact) {
      return {
        id,
        target: { id: request.id, type: request.type },
        mode: "single",
        summary: { headline: `Artifact '${request.id}' not found`, confidence: 0 },
        findings: [],
        regressions: [],
        limitations: [`${request.type} artifact '${request.id}' could not be loaded`],
        artifacts: {},
      };
    }

    const findings = this.extractFindings(artifact, request.type);
    const regressions = this.extractRegressions(artifact, request.type);
    const summary = this.buildSummary(artifact, request.type, findings);
    const limitations = this.buildLimitations(artifact, request.type);

    const result: AnalysisResult = {
      id,
      target: { id: request.id, type: request.type },
      mode: "single",
      summary,
      findings,
      regressions,
      limitations,
      artifacts: {},
    };

    // Persist report
    const reportDir = join(this.knowledgeRoot, "_analyses");
    if (!existsSync(reportDir)) mkdirSync(reportDir, { recursive: true });
    const reportPath = join(reportDir, `${request.id}_analysis.json`);
    writeFileSync(reportPath, JSON.stringify(result, null, 2), "utf-8");
    result.artifacts.reportPath = reportPath;

    return result;
  }

  /**
   * Compare two artifacts.
   */
  compare(request: CompareRequest): AnalysisResult {
    const id = generateId();
    const left = this.loadArtifact(request.left.id, request.left.type);
    const right = this.loadArtifact(request.right.id, request.right.type);

    const limitations: string[] = [];

    if (!left) limitations.push(`Left artifact '${request.left.id}' not found`);
    if (!right) limitations.push(`Right artifact '${request.right.id}' not found`);
    if (request.left.type !== request.right.type) {
      limitations.push(`Comparing different types (${request.left.type} vs ${request.right.type}) — results may be limited`);
    }

    if (!left || !right) {
      return {
        id,
        target: request.left,
        compareTarget: request.right,
        mode: "compare",
        summary: { headline: "Comparison incomplete — artifact(s) not found", confidence: 0 },
        findings: [],
        regressions: [],
        limitations,
        artifacts: {},
      };
    }

    if (request.left.type !== request.right.type) {
      return {
        id,
        target: request.left,
        compareTarget: request.right,
        mode: "compare",
        summary: { headline: "Comparison unavailable across different artifact types", confidence: 0 },
        findings: [],
        regressions: [],
        limitations,
        artifacts: {},
      };
    }

    const findings = this.compareFindings(left, right, request.left.type);
    const regressions = this.compareRegressions(left, right);
    const attribution = this.computeAttribution(left, right);
    const summary = this.buildCompareSummary(left, right, findings, regressions);

    const result: AnalysisResult = {
      id,
      target: request.left,
      compareTarget: request.right,
      mode: "compare",
      summary,
      findings,
      regressions,
      attribution,
      limitations,
      artifacts: {},
    };

    const reportDir = join(this.knowledgeRoot, "_analyses");
    if (!existsSync(reportDir)) mkdirSync(reportDir, { recursive: true });
    const reportPath = join(reportDir, `${request.left.id}_vs_${request.right.id}.json`);
    writeFileSync(reportPath, JSON.stringify(result, null, 2), "utf-8");
    result.artifacts.reportPath = reportPath;

    return result;
  }

  // -------------------------------------------------------------------------
  // Artifact loading
  // -------------------------------------------------------------------------

  private loadArtifact(id: string, type: AnalysisTargetType): Record<string, unknown> | null {
    switch (type) {
      case "simulation":
        return this.loadDirectoryArtifact(join(this.knowledgeRoot, "_simulations", id));
      case "investigation":
        return this.loadDirectoryArtifact(join(this.knowledgeRoot, "_investigations", id));
      case "mission":
        return this.loadMissionArtifact(id);
      case "run":
        return this.loadRunArtifact(id);
    }
  }

  private loadDirectoryArtifact(dir: string): Record<string, unknown> | null {
    const reportPath = join(dir, "report.json");
    if (!existsSync(reportPath)) {
      return null;
    }
    try {
      return JSON.parse(readFileSync(reportPath, "utf-8")) as Record<string, unknown>;
    } catch {
      return null;
    }
  }

  private loadMissionArtifact(id: string): Record<string, unknown> | null {
    const checkpointDir = join(this.runsRoot, "missions", id, "checkpoints");
    const latestCheckpoint = this.loadLatestCheckpoint(checkpointDir);
    let missionPayload: Record<string, unknown> | null = null;

    if (this.dbPath && existsSync(this.dbPath)) {
      const manager = new MissionManager(this.dbPath);
      try {
        const mission = manager.get(id);
        if (mission) {
          const steps = manager.steps(id);
          const subgoals = manager.subgoals(id);
          const verifications = manager.verifications(id);
          missionPayload = {
            mission,
            status: mission.status,
            steps,
            subgoals,
            verifications,
            budgetUsage: manager.budgetUsage(id),
            latestVerification: verifications.at(-1) ?? null,
          };
        }
      } finally {
        manager.close();
      }
    }

    if (missionPayload && latestCheckpoint) {
      return {
        ...missionPayload,
        checkpointDir,
        latestCheckpoint,
      };
    }

    if (missionPayload) {
      return missionPayload;
    }

    if (latestCheckpoint) {
      const checkpointMission = latestCheckpoint.mission as Record<string, unknown> | undefined;
      return {
        ...latestCheckpoint,
        status: checkpointMission?.status ?? "unknown",
        checkpointDir,
      };
    }

    return null;
  }

  private loadRunArtifact(id: string): Record<string, unknown> | null {
    if (!this.dbPath || !existsSync(this.dbPath)) {
      return null;
    }

    const store = new SQLiteStore(this.dbPath);
    try {
      const run = store.getRun(id);
      if (!run) {
        return null;
      }

      const trajectory = store.getScoreTrajectory(id);
      const latestGeneration = trajectory.at(-1) ?? null;
      const sessionReportPath = join(this.runsRoot, id, "session_report.md");
      const sessionReport = existsSync(sessionReportPath)
        ? readFileSync(sessionReportPath, "utf-8")
        : "";

      return {
        ...run,
        status: run.status,
        trajectory,
        latestGeneration,
        sessionReport,
        sessionReportPath: sessionReport ? sessionReportPath : undefined,
        summary: latestGeneration
          ? {
              score: latestGeneration.best_score,
              reasoning: sessionReport
                ? this.extractSessionReportSummary(sessionReport)
                : `Latest gate decision: ${latestGeneration.gate_decision}`,
              dimensionScores: latestGeneration.dimension_summary,
            }
          : {
              score: 0,
              reasoning: sessionReport
                ? this.extractSessionReportSummary(sessionReport)
                : `Run '${run.run_id}' has no completed generations yet`,
              dimensionScores: {},
            },
      };
    } finally {
      store.close();
    }
  }

  private loadLatestCheckpoint(checkpointDir: string): Record<string, unknown> | null {
    if (!existsSync(checkpointDir)) {
      return null;
    }

    try {
      const latest = readdirSync(checkpointDir)
        .filter((name) => name.endsWith(".json"))
        .sort()
        .pop();
      if (!latest) {
        return null;
      }
      return JSON.parse(readFileSync(join(checkpointDir, latest), "utf-8")) as Record<string, unknown>;
    } catch {
      return null;
    }
  }

  // -------------------------------------------------------------------------
  // Single-target analysis
  // -------------------------------------------------------------------------

  private extractFindings(artifact: Record<string, unknown>, type: AnalysisTargetType): Finding[] {
    const findings: Finding[] = [];

    if (type === "simulation") {
      const summary = artifact.summary as Record<string, unknown> | undefined;
      if (summary) {
        const score = Number(summary.score ?? 0);
        findings.push({
          kind: score >= 0.8 ? "observation" : score >= 0.5 ? "warning" : "regression",
          statement: `Simulation scored ${score.toFixed(2)}: ${summary.reasoning ?? ""}`,
          evidence: ["simulation score"],
        });
        const dims = summary.dimensionScores as Record<string, number> | undefined;
        if (dims) {
          for (const [dim, val] of Object.entries(dims)) {
            if (val < 0.5) {
              findings.push({
                kind: "warning",
                statement: `Weak dimension: ${dim} scored ${val.toFixed(2)}`,
                evidence: ["dimension score"],
              });
            }
          }
        }
      }
    }

    if (type === "investigation") {
      const conclusion = artifact.conclusion as Record<string, unknown> | undefined;
      if (conclusion) {
        findings.push({
          kind: "conclusion",
          statement: String(conclusion.bestExplanation ?? "No conclusion"),
          evidence: ["investigation conclusion"],
        });
      }
      const hypotheses = artifact.hypotheses as Array<Record<string, unknown>> | undefined;
      if (hypotheses) {
        for (const h of hypotheses) {
          if (h.status === "supported") {
            findings.push({
              kind: "driver",
              statement: `Supported: ${h.statement} (confidence: ${Number(h.confidence ?? 0).toFixed(2)})`,
              evidence: ["hypothesis evaluation"],
            });
          }
        }
      }
    }

    if (type === "run") {
      const scenario = String(artifact.scenario ?? "unknown");
      const status = String(artifact.status ?? "unknown");
      const summary = artifact.summary as Record<string, unknown> | undefined;
      findings.push({
        kind: status === "completed" ? "observation" : "warning",
        statement: `Run for scenario '${scenario}' is ${status}`,
        evidence: ["run metadata"],
      });
      if (summary && typeof summary.score === "number") {
        findings.push({
          kind: summary.score >= 0.8 ? "improvement" : summary.score >= 0.5 ? "observation" : "regression",
          statement: `Best generation score reached ${summary.score.toFixed(2)}`,
          evidence: ["run trajectory"],
        });
      }
    }

    if (type === "mission") {
      const mission = (artifact.mission as Record<string, unknown> | undefined) ?? artifact;
      const name = String(mission.name ?? mission.id ?? "mission");
      const status = String(mission.status ?? artifact.status ?? "unknown");
      findings.push({
        kind: status === "completed" ? "conclusion" : status === "failed" || status === "verifier_failed" ? "warning" : "observation",
        statement: `Mission '${name}' is ${status}`,
        evidence: ["mission status"],
      });
      const latestVerification = artifact.latestVerification as Record<string, unknown> | undefined;
      if (latestVerification?.reason) {
        findings.push({
          kind: latestVerification.passed === true ? "driver" : "warning",
          statement: String(latestVerification.reason),
          evidence: ["latest verification"],
        });
      }
    }

    if (findings.length === 0) {
      findings.push({
        kind: "observation",
        statement: `${type} artifact loaded with status: ${artifact.status ?? "unknown"}`,
        evidence: ["artifact metadata"],
      });
    }

    return findings;
  }

  private extractRegressions(artifact: Record<string, unknown>, type: AnalysisTargetType): string[] {
    const regressions: string[] = [];
    if (type === "simulation") {
      const summary = artifact.summary as Record<string, unknown> | undefined;
      const dims = summary?.dimensionScores as Record<string, number> | undefined;
      if (dims) {
        for (const [dim, val] of Object.entries(dims)) {
          if (val < 0.4) regressions.push(`${dim}: ${val.toFixed(2)} (below threshold)`);
        }
      }
    }
    if (type === "run") {
      const summary = artifact.summary as Record<string, unknown> | undefined;
      const dims = summary?.dimensionScores as Record<string, number> | undefined;
      if (dims) {
        for (const [dim, val] of Object.entries(dims)) {
          if (val < 0.4) regressions.push(`${dim}: ${val.toFixed(2)} (below threshold)`);
        }
      }
    }
    if (type === "mission") {
      const mission = (artifact.mission as Record<string, unknown> | undefined) ?? artifact;
      const status = String(mission.status ?? artifact.status ?? "unknown");
      if (["failed", "verifier_failed", "blocked", "budget_exhausted", "canceled"].includes(status)) {
        regressions.push(`Mission status is ${status}`);
      }
      const latestVerification = artifact.latestVerification as Record<string, unknown> | undefined;
      if (latestVerification?.passed === false && typeof latestVerification.reason === "string") {
        regressions.push(String(latestVerification.reason));
      }
    }
    return regressions;
  }

  private buildSummary(artifact: Record<string, unknown>, type: AnalysisTargetType, findings: Finding[]): AnalysisSummary {
    if (type === "simulation") {
      const summary = artifact.summary as Record<string, unknown> | undefined;
      const score = Number(summary?.score ?? 0);
      return {
        headline: `Simulation '${artifact.name ?? "unknown"}' scored ${score.toFixed(2)}`,
        confidence: Math.min(1, score * 0.9 + 0.1),
      };
    }
    if (type === "investigation") {
      const conclusion = artifact.conclusion as Record<string, unknown> | undefined;
      return {
        headline: String(conclusion?.bestExplanation ?? "Investigation analyzed"),
        confidence: Number(conclusion?.confidence ?? 0.5),
      };
    }
    if (type === "run") {
      const summary = artifact.summary as Record<string, unknown> | undefined;
      const score = Number(summary?.score ?? 0);
      return {
        headline: `Run '${artifact.run_id ?? "unknown"}' for scenario '${artifact.scenario ?? "unknown"}' reached ${score.toFixed(2)}`,
        confidence: Math.min(1, Math.max(0.2, score)),
      };
    }
    if (type === "mission") {
      const mission = (artifact.mission as Record<string, unknown> | undefined) ?? artifact;
      const status = String(mission.status ?? artifact.status ?? "unknown");
      return {
        headline: `Mission '${mission.name ?? mission.id ?? "unknown"}' is ${status}`,
        confidence: status === "completed" ? 0.9 : status === "active" || status === "paused" ? 0.6 : 0.7,
      };
    }
    return {
      headline: `${type} artifact analyzed — ${findings.length} finding(s)`,
      confidence: 0.5,
    };
  }

  private buildLimitations(artifact: Record<string, unknown>, type: AnalysisTargetType): string[] {
    const limitations: string[] = [];
    if (type === "simulation") {
      limitations.push("Analysis based on simulation output, not empirical data");
      const warnings = artifact.warnings as string[] | undefined;
      if (warnings) limitations.push(...warnings);
    }
    if (type === "investigation") {
      limitations.push("Analysis based on generated investigation scenario");
      const lims = (artifact.conclusion as Record<string, unknown> | undefined)?.limitations as string[] | undefined;
      if (lims) limitations.push(...lims);
    }
    if (type === "run") {
      limitations.push("Analysis based on stored run trajectory and session report");
      if (!artifact.sessionReport) {
        limitations.push("No session report was available for this run");
      }
    }
    if (type === "mission") {
      limitations.push("Analysis based on mission state and latest checkpoint");
      if (!(artifact.latestCheckpoint as Record<string, unknown> | undefined)) {
        limitations.push("No mission checkpoint was available");
      }
    }
    limitations.push("Heuristic attribution — not causal proof");
    return limitations;
  }

  // -------------------------------------------------------------------------
  // Compare mode
  // -------------------------------------------------------------------------

  private compareFindings(
    left: Record<string, unknown>, right: Record<string, unknown>, type: AnalysisTargetType,
  ): Finding[] {
    const findings: Finding[] = [];
    const leftScore = this.extractScore(left);
    const rightScore = this.extractScore(right);

    if (leftScore != null && rightScore != null) {
      const delta = rightScore - leftScore;
      if (Math.abs(delta) > 0.01) {
        findings.push({
          kind: delta > 0 ? "improvement" : "regression",
          statement: `Score changed from ${leftScore.toFixed(2)} to ${rightScore.toFixed(2)} (${delta > 0 ? "+" : ""}${delta.toFixed(2)})`,
          evidence: ["score comparison"],
        });
      }
    }

    // Dimension-level comparison
    const leftDims = this.extractDimensionScores(left);
    const rightDims = this.extractDimensionScores(right);
    for (const dim of new Set([...Object.keys(leftDims), ...Object.keys(rightDims)])) {
      const lv = leftDims[dim] ?? 0;
      const rv = rightDims[dim] ?? 0;
      const delta = rv - lv;
      if (Math.abs(delta) > 0.05) {
        findings.push({
          kind: delta > 0 ? "improvement" : "regression",
          statement: `${dim}: ${lv.toFixed(2)} → ${rv.toFixed(2)} (${delta > 0 ? "+" : ""}${delta.toFixed(2)})`,
          evidence: ["dimension comparison"],
        });
      }
    }

    if (findings.length === 0) {
      findings.push({ kind: "observation", statement: "No significant differences found", evidence: ["comparison"] });
    }

    return findings;
  }

  private compareRegressions(left: Record<string, unknown>, right: Record<string, unknown>): string[] {
    const regressions: string[] = [];
    const leftScore = this.extractScore(left) ?? 0;
    const rightScore = this.extractScore(right) ?? 0;
    if (rightScore < leftScore - 0.05) {
      regressions.push(`Overall score regressed from ${leftScore.toFixed(2)} to ${rightScore.toFixed(2)}`);
    }
    const leftDims = this.extractDimensionScores(left);
    const rightDims = this.extractDimensionScores(right);
    for (const dim of Object.keys(leftDims)) {
      if ((rightDims[dim] ?? 0) < leftDims[dim] - 0.1) {
        regressions.push(`${dim} regressed from ${leftDims[dim].toFixed(2)} to ${(rightDims[dim] ?? 0).toFixed(2)}`);
      }
    }
    return regressions;
  }

  private computeAttribution(left: Record<string, unknown>, right: Record<string, unknown>): Attribution {
    const leftDims = this.extractDimensionScores(left);
    const rightDims = this.extractDimensionScores(right);
    const factors: Array<{ name: string; weight: number }> = [];

    for (const dim of new Set([...Object.keys(leftDims), ...Object.keys(rightDims)])) {
      const delta = Math.abs((rightDims[dim] ?? 0) - (leftDims[dim] ?? 0));
      if (delta > 0.01) {
        factors.push({ name: dim, weight: Math.round(delta * 100) / 100 });
      }
    }

    factors.sort((a, b) => b.weight - a.weight);
    return { topFactors: factors.length > 0 ? factors : [{ name: "overall_score", weight: 1 }] };
  }

  private buildCompareSummary(
    left: Record<string, unknown>, right: Record<string, unknown>,
    findings: Finding[], regressions: string[],
  ): AnalysisSummary {
    const leftScore = this.extractScore(left);
    const rightScore = this.extractScore(right);
    const improvements = findings.filter((f) => f.kind === "improvement").length;
    const regCount = regressions.length;

    let headline: string;
    if (leftScore != null && rightScore != null) {
      const delta = rightScore - leftScore;
      headline = delta > 0
        ? `Score improved by ${delta.toFixed(2)} (${leftScore.toFixed(2)} → ${rightScore.toFixed(2)}) with ${improvements} improvement(s)`
        : delta < 0
        ? `Score regressed by ${Math.abs(delta).toFixed(2)} (${leftScore.toFixed(2)} → ${rightScore.toFixed(2)}) with ${regCount} regression(s)`
        : `Score unchanged at ${leftScore.toFixed(2)}`;
    } else {
      headline = `Comparison: ${findings.length} finding(s), ${regCount} regression(s)`;
    }

    return { headline, confidence: 0.7 };
  }

  // -------------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------------

  private extractScore(artifact: Record<string, unknown>): number | null {
    const summary = artifact.summary as Record<string, unknown> | undefined;
    if (summary && typeof summary.score === "number") return summary.score;
    const conclusion = artifact.conclusion as Record<string, unknown> | undefined;
    if (conclusion && typeof conclusion.confidence === "number") return conclusion.confidence;
    return null;
  }

  private extractDimensionScores(artifact: Record<string, unknown>): Record<string, number> {
    const summary = artifact.summary as Record<string, unknown> | undefined;
    const dims = summary?.dimensionScores;
    if (dims && typeof dims === "object" && !Array.isArray(dims)) {
      return dims as Record<string, number>;
    }
    return {};
  }

  private extractSessionReportSummary(report: string): string {
    const lines = report
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.length > 0 && !line.startsWith("#"));
    return lines.slice(0, 2).join(" ").trim() || "Session report available";
  }
}
