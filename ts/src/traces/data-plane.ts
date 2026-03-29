/**
 * Trace-to-disposable-model data plane (AC-466).
 *
 * Orchestrates the pipeline from raw traces → curated dataset → training inputs.
 *
 * DatasetCurator: filters, scores, splits held-out, enforces consent.
 * DataPlane: ingest → curate → output ShareGPT JSONL + manifest.
 *
 * This is the program-level orchestrator that ties AC-462 (schema),
 * AC-464 (redaction), AC-463 (export), AC-465 (publishing) together
 * into a single dataset construction pipeline.
 */

import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync, appendFileSync } from "node:fs";
import { join } from "node:path";
import type { PublicTrace, ProvenanceManifest, SubmissionAttestation } from "./public-schema.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface TraceEntry {
  trace: PublicTrace;
  manifest: ProvenanceManifest;
  attestation: SubmissionAttestation;
}

export interface CurationPolicy {
  /** Minimum outcome score to include (default: 0, include all) */
  minScore?: number;
  /** Fraction of included traces to hold out for evaluation (default: 0) */
  heldOutRatio?: number;
  /** Only include traces with allowTraining consent (default: true) */
  requireTrainingConsent?: boolean;
}

export interface CuratedDataset {
  included: TraceEntry[];
  excluded: TraceEntry[];
  train: TraceEntry[];
  heldOut: TraceEntry[];
}

export interface DataPlaneConfig {
  traceDir: string;
  outputDir: string;
  curationPolicy?: CurationPolicy;
}

export interface DataPlaneBuildResult {
  status: "completed" | "failed";
  totalTraces: number;
  includedTraces: number;
  excludedTraces: number;
  trainSize: number;
  heldOutSize: number;
  outputDir: string;
  error?: string;
}

export interface DataPlaneStatus {
  totalTraces: number;
  includedTraces: number;
  trainSize: number;
  heldOutSize: number;
  outputDir: string;
  built: boolean;
}

// ---------------------------------------------------------------------------
// DatasetCurator
// ---------------------------------------------------------------------------

export class DatasetCurator {
  private policy: Required<CurationPolicy>;

  constructor(policy?: CurationPolicy) {
    this.policy = {
      minScore: policy?.minScore ?? 0,
      heldOutRatio: policy?.heldOutRatio ?? 0,
      requireTrainingConsent: policy?.requireTrainingConsent ?? true,
    };
  }

  curate(traceDir: string): CuratedDataset {
    const entries = this.loadEntries(traceDir);
    const included: TraceEntry[] = [];
    const excluded: TraceEntry[] = [];

    for (const entry of entries) {
      if (this.shouldInclude(entry)) {
        included.push(entry);
      } else {
        excluded.push(entry);
      }
    }

    // Split held-out
    const { train, heldOut } = this.splitHeldOut(included);

    return { included, excluded, train, heldOut };
  }

  private shouldInclude(entry: TraceEntry): boolean {
    // Consent check
    if (this.policy.requireTrainingConsent && !entry.attestation.allowTraining) {
      return false;
    }

    // Score check
    const score = (entry.trace.outcome as { score?: number } | undefined)?.score;
    if (score != null && score < this.policy.minScore) {
      return false;
    }

    return true;
  }

  private splitHeldOut(entries: TraceEntry[]): { train: TraceEntry[]; heldOut: TraceEntry[] } {
    if (this.policy.heldOutRatio <= 0 || entries.length <= 1) {
      return { train: [...entries], heldOut: [] };
    }

    const heldOutCount = Math.max(1, Math.floor(entries.length * this.policy.heldOutRatio));
    // Deterministic split: last N entries become held-out
    const train = entries.slice(0, entries.length - heldOutCount);
    const heldOut = entries.slice(entries.length - heldOutCount);

    return { train, heldOut };
  }

  private loadEntries(traceDir: string): TraceEntry[] {
    if (!existsSync(traceDir)) return [];

    const entries: TraceEntry[] = [];
    let files: string[];
    try {
      files = readdirSync(traceDir).filter((f) => f.endsWith(".json")).sort();
    } catch {
      return [];
    }

    for (const file of files) {
      try {
        const raw = readFileSync(join(traceDir, file), "utf-8");
        const parsed = JSON.parse(raw) as TraceEntry;
        if (parsed.trace?.traceId && parsed.manifest && parsed.attestation) {
          entries.push(parsed);
        }
      } catch {
        // Skip malformed files
      }
    }

    return entries;
  }
}

// ---------------------------------------------------------------------------
// ShareGPT conversion
// ---------------------------------------------------------------------------

function toShareGPT(trace: PublicTrace): Record<string, unknown> {
  const roleMap: Record<string, string> = {
    user: "human",
    assistant: "gpt",
    system: "system",
    tool: "tool",
  };

  return {
    conversations: trace.messages.map((m) => ({
      from: roleMap[m.role] ?? m.role,
      value: m.content,
    })),
    metadata: {
      traceId: trace.traceId,
      sourceHarness: trace.sourceHarness,
      score: (trace.outcome as { score?: number } | undefined)?.score,
    },
  };
}

// ---------------------------------------------------------------------------
// DataPlane orchestrator
// ---------------------------------------------------------------------------

export class DataPlane {
  private config: DataPlaneConfig;
  private lastResult?: DataPlaneBuildResult;

  constructor(config: DataPlaneConfig) {
    this.config = config;
  }

  async build(): Promise<DataPlaneBuildResult> {
    try {
      const curator = new DatasetCurator(this.config.curationPolicy);
      const dataset = curator.curate(this.config.traceDir);

      if (!existsSync(this.config.outputDir)) {
        mkdirSync(this.config.outputDir, { recursive: true });
      }

      // Write train.jsonl
      const trainPath = join(this.config.outputDir, "train.jsonl");
      writeFileSync(trainPath, "", "utf-8"); // clear
      for (const entry of dataset.train) {
        const line = JSON.stringify(toShareGPT(entry.trace)) + "\n";
        appendFileSync(trainPath, line, "utf-8");
      }

      // Write held_out.jsonl
      if (dataset.heldOut.length > 0) {
        const heldOutPath = join(this.config.outputDir, "held_out.jsonl");
        writeFileSync(heldOutPath, "", "utf-8");
        for (const entry of dataset.heldOut) {
          appendFileSync(heldOutPath, JSON.stringify(toShareGPT(entry.trace)) + "\n", "utf-8");
        }
      }

      // Write manifest
      const sources = new Map<string, number>();
      for (const entry of dataset.included) {
        const src = entry.manifest.sourceHarness;
        sources.set(src, (sources.get(src) ?? 0) + 1);
      }

      const manifest = {
        totalTraces: dataset.included.length + dataset.excluded.length,
        includedTraces: dataset.included.length,
        excludedTraces: dataset.excluded.length,
        trainSize: dataset.train.length,
        heldOutSize: dataset.heldOut.length,
        curationPolicy: this.config.curationPolicy ?? {},
        sources: Object.fromEntries(sources),
        createdAt: new Date().toISOString(),
      };

      writeFileSync(
        join(this.config.outputDir, "manifest.json"),
        JSON.stringify(manifest, null, 2),
        "utf-8",
      );

      const result: DataPlaneBuildResult = {
        status: "completed",
        totalTraces: manifest.totalTraces,
        includedTraces: manifest.includedTraces,
        excludedTraces: manifest.excludedTraces,
        trainSize: manifest.trainSize,
        heldOutSize: manifest.heldOutSize,
        outputDir: this.config.outputDir,
      };

      this.lastResult = result;
      return result;
    } catch (err) {
      const result: DataPlaneBuildResult = {
        status: "failed",
        totalTraces: 0, includedTraces: 0, excludedTraces: 0,
        trainSize: 0, heldOutSize: 0,
        outputDir: this.config.outputDir,
        error: err instanceof Error ? err.message : String(err),
      };
      this.lastResult = result;
      return result;
    }
  }

  status(): DataPlaneStatus {
    return {
      totalTraces: this.lastResult?.totalTraces ?? 0,
      includedTraces: this.lastResult?.includedTraces ?? 0,
      trainSize: this.lastResult?.trainSize ?? 0,
      heldOutSize: this.lastResult?.heldOutSize ?? 0,
      outputDir: this.config.outputDir,
      built: this.lastResult?.status === "completed",
    };
  }
}
