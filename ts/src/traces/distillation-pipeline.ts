/**
 * Curated distillation dataset pipeline (AC-458).
 *
 * Extends the basic DataPlane with richer curation policies:
 * - Gate-based filtering (advance-only)
 * - Top-quartile selection
 * - Scenario-family filtering
 * - Failure-example policy (exclude, eval_only, contrastive)
 * - Source provenance tracking per trace
 * - Rich distillation manifest
 */

import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync, appendFileSync } from "node:fs";
import { join } from "node:path";
import type { PublicTrace, ProvenanceManifest, SubmissionAttestation } from "./public-schema.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface TraceEntry {
  trace: PublicTrace;
  manifest: ProvenanceManifest;
  attestation: SubmissionAttestation;
}

export type FailurePolicy = "exclude" | "eval_only" | "contrastive";

export interface DistillationPolicy {
  minScore?: number;
  topQuartile?: boolean;
  advanceOnly?: boolean;
  familyFilter?: string[];
  heldOutRatio?: number;
  failurePolicy?: FailurePolicy;
  requireTrainingConsent?: boolean;
}

export interface DistillationManifest {
  totalTraces: number;
  includedTraces: number;
  excludedTraces: number;
  trainSize: number;
  heldOutSize: number;
  evalOnlySize: number;
  contrastiveSize: number;
  curationPolicy: DistillationPolicy;
  sources: Record<string, number>;
  createdAt: string;
}

export interface DistillationResult {
  status: "completed" | "failed";
  totalTraces: number;
  includedTraces: number;
  excludedTraces: number;
  trainSize: number;
  heldOutSize: number;
  evalOnlyTraces: number;
  contrastiveTraces: number;
  outputDir: string;
  /** Files that couldn't be parsed during loading */
  warnings: string[];
  error?: string;
}

export interface DistillationPipelineConfig {
  traceDir: string;
  outputDir: string;
  policy?: DistillationPolicy;
}

// ---------------------------------------------------------------------------
// ShareGPT conversion
// ---------------------------------------------------------------------------

function toShareGPT(trace: PublicTrace, extraMetadata?: Record<string, unknown>): Record<string, unknown> {
  const roleMap: Record<string, string> = {
    user: "human", assistant: "gpt", system: "system", tool: "tool",
  };
  return {
    conversations: trace.messages.map((m) => ({
      from: roleMap[m.role] ?? m.role,
      value: m.content,
    })),
    metadata: {
      traceId: trace.traceId,
      sourceHarness: trace.sourceHarness,
      score: (trace.outcome as Record<string, unknown> | undefined)?.score,
      ...extraMetadata,
    },
  };
}

// ---------------------------------------------------------------------------
// Pipeline
// ---------------------------------------------------------------------------

export class DistillationPipeline {
  private config: DistillationPipelineConfig;
  private policy: Required<DistillationPolicy>;

  constructor(config: DistillationPipelineConfig) {
    this.config = config;
    this.policy = {
      minScore: config.policy?.minScore ?? 0,
      topQuartile: config.policy?.topQuartile ?? false,
      advanceOnly: config.policy?.advanceOnly ?? false,
      familyFilter: config.policy?.familyFilter ?? [],
      heldOutRatio: config.policy?.heldOutRatio ?? 0,
      failurePolicy: config.policy?.failurePolicy ?? "exclude",
      requireTrainingConsent: config.policy?.requireTrainingConsent ?? true,
    };
  }

  build(): DistillationResult {
    const warnings: string[] = [];
    try {
      const loaded = this.loadEntries();
      warnings.push(...loaded.warnings);
      const entries = loaded.entries;
      const { included, excluded, evalOnly, contrastive } = this.applyPolicy(entries);
      const { train, heldOut } = this.splitHeldOut(included);

      if (!existsSync(this.config.outputDir)) mkdirSync(this.config.outputDir, { recursive: true });

      // Write train.jsonl
      this.writeJSONL(join(this.config.outputDir, "train.jsonl"), train);

      // Write held_out.jsonl
      if (heldOut.length > 0) {
        this.writeJSONL(join(this.config.outputDir, "held_out.jsonl"), heldOut);
      }

      // Write eval_only.jsonl
      if (evalOnly.length > 0) {
        this.writeJSONL(join(this.config.outputDir, "eval_only.jsonl"), evalOnly);
      }

      // Write contrastive.jsonl
      if (contrastive.length > 0) {
        this.writeJSONL(join(this.config.outputDir, "contrastive.jsonl"), contrastive, {
          examplePolicy: "contrastive",
        });
      }

      // Write manifest
      const sources: Record<string, number> = {};
      for (const e of included) {
        const src = e.manifest.sourceHarness;
        sources[src] = (sources[src] ?? 0) + 1;
      }

      const manifest: DistillationManifest = {
        totalTraces: entries.length,
        includedTraces: included.length,
        excludedTraces: excluded.length,
        trainSize: train.length,
        heldOutSize: heldOut.length,
        evalOnlySize: evalOnly.length,
        contrastiveSize: contrastive.length,
        curationPolicy: this.config.policy ?? {},
        sources,
        createdAt: new Date().toISOString(),
      };
      writeFileSync(join(this.config.outputDir, "manifest.json"), JSON.stringify(manifest, null, 2), "utf-8");

      return {
        status: "completed",
        totalTraces: entries.length,
        includedTraces: included.length,
        excludedTraces: excluded.length,
        trainSize: train.length,
        heldOutSize: heldOut.length,
        evalOnlyTraces: evalOnly.length,
        contrastiveTraces: contrastive.length,
        outputDir: this.config.outputDir,
        warnings,
      };
    } catch (err) {
      return {
        status: "failed",
        totalTraces: 0, includedTraces: 0, excludedTraces: 0,
        trainSize: 0, heldOutSize: 0, evalOnlyTraces: 0, contrastiveTraces: 0,
        outputDir: this.config.outputDir,
        error: err instanceof Error ? err.message : String(err),
        warnings,
      };
    }
  }

  // -------------------------------------------------------------------------
  // Policy application
  // -------------------------------------------------------------------------

  private applyPolicy(entries: TraceEntry[]): {
    included: TraceEntry[];
    excluded: TraceEntry[];
    evalOnly: TraceEntry[];
    contrastive: TraceEntry[];
  } {
    let candidates = entries;

    // Consent filter
    if (this.policy.requireTrainingConsent) {
      candidates = candidates.filter((e) => e.attestation.allowTraining);
    }

    // Gate filter (advance-only)
    if (this.policy.advanceOnly) {
      candidates = candidates.filter((e) => {
        const gate = (e.trace.metadata as Record<string, unknown> | undefined)?.gateDecision;
        return gate === "advance";
      });
    }

    // Family filter
    if (this.policy.familyFilter.length > 0) {
      const families = new Set(this.policy.familyFilter);
      candidates = candidates.filter((e) => {
        const family = (e.trace.metadata as Record<string, unknown> | undefined)?.family;
        return typeof family === "string" && families.has(family);
      });
    }

    // Score-based filtering
    const scoreThreshold = this.policy.topQuartile
      ? this.computeTopQuartileThreshold(candidates)
      : this.policy.minScore;

    const included: TraceEntry[] = [];
    const excluded: TraceEntry[] = [];
    const evalOnly: TraceEntry[] = [];
    const contrastive: TraceEntry[] = [];

    for (const e of candidates) {
      const score = (e.trace.outcome as Record<string, unknown> | undefined)?.score as number | undefined;
      const passes = score == null || score >= scoreThreshold;

      if (passes) {
        included.push(e);
      } else if (this.policy.failurePolicy === "eval_only") {
        evalOnly.push(e);
      } else if (this.policy.failurePolicy === "contrastive") {
        contrastive.push(e);
      } else {
        excluded.push(e);
      }
    }

    // Also track entries removed by consent/gate/family as excluded
    const allExcluded = [...excluded, ...entries.filter((e) => !candidates.includes(e))];

    return { included, excluded: allExcluded, evalOnly, contrastive };
  }

  private computeTopQuartileThreshold(entries: TraceEntry[]): number {
    const scores = entries
      .map((e) => (e.trace.outcome as Record<string, unknown> | undefined)?.score)
      .filter((s): s is number => typeof s === "number")
      .sort((a, b) => a - b);

    if (scores.length === 0) return 0;
    const q75Index = Math.floor(scores.length * 0.75);
    return scores[q75Index] ?? scores[scores.length - 1];
  }

  private splitHeldOut(entries: TraceEntry[]): { train: TraceEntry[]; heldOut: TraceEntry[] } {
    if (this.policy.heldOutRatio <= 0 || entries.length <= 1) {
      return { train: [...entries], heldOut: [] };
    }
    const heldOutCount = Math.max(1, Math.floor(entries.length * this.policy.heldOutRatio));
    return {
      train: entries.slice(0, entries.length - heldOutCount),
      heldOut: entries.slice(entries.length - heldOutCount),
    };
  }

  // -------------------------------------------------------------------------
  // I/O
  // -------------------------------------------------------------------------

  private loadEntries(): { entries: TraceEntry[]; warnings: string[] } {
    if (!existsSync(this.config.traceDir)) {
      return { entries: [], warnings: [] };
    }
    const entries: TraceEntry[] = [];
    const warnings: string[] = [];
    let files: string[];
    try {
      files = readdirSync(this.config.traceDir).filter((f) => f.endsWith(".json")).sort();
    } catch (err) {
      warnings.push(`Could not read trace directory '${this.config.traceDir}': ${err instanceof Error ? err.message : String(err)}`);
      return { entries, warnings };
    }

    for (const file of files) {
      try {
        const raw = readFileSync(join(this.config.traceDir, file), "utf-8");
        const parsed = JSON.parse(raw) as TraceEntry;
        if (parsed.trace?.traceId && parsed.manifest && parsed.attestation) {
          entries.push(parsed);
        } else {
          warnings.push(`${file}: missing trace, manifest, or attestation`);
        }
      } catch (err) {
        warnings.push(`${file}: ${err instanceof Error ? err.message : "parse error"}`);
      }
    }
    return { entries, warnings };
  }

  private writeJSONL(
    path: string,
    entries: TraceEntry[],
    extraMetadata?: Record<string, unknown>,
  ): void {
    writeFileSync(path, "", "utf-8");
    for (const e of entries) {
      appendFileSync(path, JSON.stringify(toShareGPT(e.trace, extraMetadata)) + "\n", "utf-8");
    }
  }
}
