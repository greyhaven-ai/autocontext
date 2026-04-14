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

import { join } from "node:path";

import {
  applyDistillationPolicy,
  normalizeDistillationPolicy,
  splitHeldOutEntries,
  summarizeSources,
  type NormalizedDistillationPolicy,
} from "./distillation-curation-workflow.js";
import {
  buildDistillationManifest,
  ensureDistillationOutputDir,
  loadDistillationEntries,
  writeDistillationJsonl,
  writeDistillationManifest,
} from "./distillation-io-workflow.js";
import type {
  DistillationManifest,
  DistillationPipelineConfig,
  DistillationPolicy,
  DistillationResult,
  FailurePolicy,
} from "./distillation-types.js";

export type {
  DistillationManifest,
  DistillationPipelineConfig,
  DistillationPolicy,
  DistillationResult,
  FailurePolicy,
} from "./distillation-types.js";

export class DistillationPipeline {
  private config: DistillationPipelineConfig;
  private policy: NormalizedDistillationPolicy;

  constructor(config: DistillationPipelineConfig) {
    this.config = config;
    this.policy = normalizeDistillationPolicy(config.policy);
  }

  build(): DistillationResult {
    const warnings: string[] = [];

    try {
      const loaded = loadDistillationEntries(this.config.traceDir);
      warnings.push(...loaded.warnings);

      const buckets = applyDistillationPolicy(loaded.entries, this.policy);
      const split = splitHeldOutEntries(buckets.included, this.policy.heldOutRatio);

      ensureDistillationOutputDir(this.config.outputDir);
      writeDistillationJsonl(join(this.config.outputDir, "train.jsonl"), split.train);

      if (split.heldOut.length > 0) {
        writeDistillationJsonl(join(this.config.outputDir, "held_out.jsonl"), split.heldOut);
      }
      if (buckets.evalOnly.length > 0) {
        writeDistillationJsonl(join(this.config.outputDir, "eval_only.jsonl"), buckets.evalOnly);
      }
      if (buckets.contrastive.length > 0) {
        writeDistillationJsonl(
          join(this.config.outputDir, "contrastive.jsonl"),
          buckets.contrastive,
          { examplePolicy: "contrastive" },
        );
      }

      const manifest: DistillationManifest = buildDistillationManifest({
        totalTraces: loaded.entries.length,
        includedTraces: buckets.included.length,
        excludedTraces: buckets.excluded.length,
        trainSize: split.train.length,
        heldOutSize: split.heldOut.length,
        evalOnlySize: buckets.evalOnly.length,
        contrastiveSize: buckets.contrastive.length,
        curationPolicy: this.config.policy ?? {},
        sources: summarizeSources(buckets.included),
      });
      writeDistillationManifest(this.config.outputDir, manifest);

      return {
        status: "completed",
        totalTraces: loaded.entries.length,
        includedTraces: buckets.included.length,
        excludedTraces: buckets.excluded.length,
        trainSize: split.train.length,
        heldOutSize: split.heldOut.length,
        evalOnlyTraces: buckets.evalOnly.length,
        contrastiveTraces: buckets.contrastive.length,
        outputDir: this.config.outputDir,
        warnings,
      };
    } catch (err) {
      return {
        status: "failed",
        totalTraces: 0,
        includedTraces: 0,
        excludedTraces: 0,
        trainSize: 0,
        heldOutSize: 0,
        evalOnlyTraces: 0,
        contrastiveTraces: 0,
        outputDir: this.config.outputDir,
        error: err instanceof Error ? err.message : String(err),
        warnings,
      };
    }
  }
}
