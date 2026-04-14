/**
 * Privacy-aware trace export workflow (AC-463).
 *
 * Packages autocontext sessions for public sharing:
 * 1. Load run artifacts (generations, prompts, outputs)
 * 2. Convert to public trace schema
 * 3. Run redaction pipeline
 * 4. Generate provenance manifest + submission attestation
 * 5. Write reviewed, redacted artifact
 *
 * Integrates AC-462 (public schema) and AC-464 (redaction).
 */

import { existsSync } from "node:fs";
import { join } from "node:path";

import {
  blockExportResult,
  buildPublicTracePackage,
  failExportResult,
  validateExportTrace,
  writeExportArtifact,
} from "./export-package-workflow.js";
import { redactTraceMessages, emptyRedactionSummary } from "./export-redaction-workflow.js";
import { loadRunMessagesFromArtifacts } from "./export-run-artifact-workflow.js";
import type {
  ExportRequest,
  ExportResult,
  RedactionSummary,
  TraceExportWorkflowOpts,
} from "./export-workflow-types.js";
import {
  SensitiveDataDetector,
  RedactionPolicy,
} from "./redaction.js";

export type {
  ExportRequest,
  ExportResult,
  RedactionSummary,
  TraceExportWorkflowOpts,
} from "./export-workflow-types.js";

export class TraceExportWorkflow {
  private runsRoot: string;
  private outputDir: string;
  private detector: SensitiveDataDetector;
  private policy: RedactionPolicy;

  constructor(opts: TraceExportWorkflowOpts) {
    this.runsRoot = opts.runsRoot;
    this.outputDir = opts.outputDir;
    this.detector = new SensitiveDataDetector();
    this.policy = new RedactionPolicy({ overrides: opts.policyOverrides });
  }

  async export(request: ExportRequest): Promise<ExportResult> {
    const traceId = `trace_${request.runId}_${Date.now().toString(36)}`;
    const warnings: string[] = [];
    const emptySummary = emptyRedactionSummary();

    if (!request.consentGiven) {
      return blockExportResult({
        traceId,
        redactionSummary: emptySummary,
        warnings,
        error: "Trace export requires explicit consent from the submitter.",
      });
    }

    if (!request.allowRedistribution) {
      return blockExportResult({
        traceId,
        redactionSummary: emptySummary,
        warnings,
        error: "Trace export requires redistribution rights for public sharing.",
      });
    }

    const runDir = join(this.runsRoot, request.runId);
    if (!existsSync(runDir)) {
      return failExportResult({
        traceId,
        redactionSummary: emptySummary,
        warnings,
        error: `Run '${request.runId}' not found at ${runDir}`,
      });
    }

    const loaded = loadRunMessagesFromArtifacts(runDir);
    warnings.push(...loaded.warnings);

    const redacted = redactTraceMessages({
      messages: loaded.messages,
      detector: this.detector,
      policy: this.policy,
    });
    if (redacted.redactionSummary.blocked) {
      return {
        status: "blocked",
        traceId,
        redactionSummary: redacted.redactionSummary,
        warnings,
      };
    }

    const pkg = buildPublicTracePackage({
      traceId,
      request,
      messages: redacted.redactedMessages,
      redactionSummary: redacted.redactionSummary,
    });
    const validation = validateExportTrace(pkg.trace);
    if (!validation.valid) {
      return failExportResult({
        traceId,
        redactionSummary: redacted.redactionSummary,
        warnings,
        error: validation.error ?? "Trace validation failed",
      });
    }

    const outputPath = writeExportArtifact(this.outputDir, traceId, pkg);
    return {
      status: "completed",
      traceId,
      outputPath,
      redactionSummary: redacted.redactionSummary,
      warnings,
    };
  }
}
