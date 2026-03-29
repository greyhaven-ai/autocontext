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

import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import {
  SCHEMA_VERSION,
  createProvenanceManifest,
  createSubmissionAttestation,
  validatePublicTrace,
  type PublicTrace,
  type TraceMessage,
} from "./public-schema.js";
import {
  SensitiveDataDetector,
  RedactionPolicy,
  applyRedactionPolicy,
  type PolicyAction,
} from "./redaction.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ExportRequest {
  runId: string;
  scenario: string;
  submitterId: string;
  license: string;
  consentGiven: boolean;
  dataOrigin: string;
  allowRedistribution: boolean;
  allowTraining: boolean;
  consentNotes?: string;
}

export interface RedactionSummary {
  totalDetections: number;
  totalRedactions: number;
  blocked: boolean;
  blockReasons: string[];
  categoryCounts: Record<string, number>;
}

export interface ExportResult {
  status: "completed" | "blocked" | "failed";
  traceId: string;
  outputPath?: string;
  redactionSummary: RedactionSummary;
  error?: string;
}

export interface TraceExportWorkflowOpts {
  runsRoot: string;
  outputDir: string;
  policyOverrides?: Record<string, PolicyAction>;
}

// ---------------------------------------------------------------------------
// Workflow
// ---------------------------------------------------------------------------

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

    if (!request.consentGiven) {
      return {
        status: "blocked",
        traceId,
        redactionSummary: emptyRedactionSummary(),
        error: "Trace export requires explicit consent from the submitter.",
      };
    }

    if (!request.allowRedistribution) {
      return {
        status: "blocked",
        traceId,
        redactionSummary: emptyRedactionSummary(),
        error: "Trace export requires redistribution rights for public sharing.",
      };
    }
    // Step 1: Load run artifacts
    const runDir = join(this.runsRoot, request.runId);
    if (!existsSync(runDir)) {
      return {
        status: "failed", traceId,
        redactionSummary: emptyRedactionSummary(),
        error: `Run '${request.runId}' not found at ${runDir}`,
      };
    }

    const messages = this.loadRunMessages(runDir, request.scenario);

    // Step 2: Run redaction on all message content
    let blocked = false;
    const allBlockReasons: string[] = [];
    let totalDetections = 0;
    let totalRedactions = 0;
    const categoryCounts: Record<string, number> = {};

    const redactedMessages: TraceMessage[] = [];
    for (const msg of messages) {
      const result = applyRedactionPolicy(msg.content, {
        detector: this.detector,
        policy: this.policy,
      });

      if (result.blocked) {
        blocked = true;
        allBlockReasons.push(...result.blockReasons);
      }

      totalDetections += result.detections.length;
      totalRedactions += result.redactions.length;
      for (const d of result.detections) {
        categoryCounts[d.category] = (categoryCounts[d.category] ?? 0) + 1;
      }

      redactedMessages.push({
        ...msg,
        content: result.redactedText,
      });
    }

    const redactionSummary: RedactionSummary = {
      totalDetections,
      totalRedactions,
      blocked,
      blockReasons: allBlockReasons,
      categoryCounts,
    };

    // Step 3: If blocked, abort
    if (blocked) {
      return { status: "blocked", traceId, redactionSummary };
    }

    // Step 4: Build public trace
    const trace: PublicTrace = {
      schemaVersion: SCHEMA_VERSION,
      traceId,
      sessionId: request.runId,
      sourceHarness: "autocontext",
      collectedAt: new Date().toISOString(),
      messages: redactedMessages,
      metadata: {
        scenario: request.scenario,
        exportedAt: new Date().toISOString(),
      },
    };

    // Validate
    const validation = validatePublicTrace(trace);
    if (!validation.valid) {
      return {
        status: "failed", traceId, redactionSummary,
        error: `Trace validation failed: ${validation.errors.join("; ")}`,
      };
    }

    // Step 5: Build manifest + attestation
    const manifest = createProvenanceManifest({
      sourceHarness: "autocontext",
      collectionMethod: "automated_harness_run",
      license: request.license,
      traceCount: 1,
      redactionPolicy: {
        applied: totalRedactions > 0,
        methods: ["regex_pattern"],
        categories: Object.keys(categoryCounts),
      },
    });

    const attestation = createSubmissionAttestation({
      submitterId: request.submitterId,
      consentGiven: request.consentGiven,
      dataOrigin: request.dataOrigin,
      allowRedistribution: request.allowRedistribution,
      allowTraining: request.allowTraining,
      notes: request.consentNotes,
    });

    // Step 6: Write artifact
    if (!existsSync(this.outputDir)) {
      mkdirSync(this.outputDir, { recursive: true });
    }

    const pkg = { trace, manifest, attestation, redactionSummary };
    const outputPath = join(this.outputDir, `${traceId}.json`);
    writeFileSync(outputPath, JSON.stringify(pkg, null, 2), "utf-8");

    return { status: "completed", traceId, outputPath, redactionSummary };
  }

  // -------------------------------------------------------------------------
  // Load run artifacts into messages
  // -------------------------------------------------------------------------

  private loadRunMessages(runDir: string, scenario: string): TraceMessage[] {
    const messages: TraceMessage[] = [];
    const timestamp = new Date().toISOString();

    // Load run metadata if available
    const metaPath = join(runDir, "run_meta.json");
    if (existsSync(metaPath)) {
      try {
        const meta = JSON.parse(readFileSync(metaPath, "utf-8"));
        messages.push({
          role: "system",
          content: `Run ${meta.run_id} for scenario ${meta.scenario}`,
          timestamp: meta.created_at ?? timestamp,
        });
      } catch { /* skip */ }
    }

    // Load generation artifacts
    const genDir = join(runDir, "generations");
    if (!existsSync(genDir)) return messages;

    let genEntries: string[];
    try {
      genEntries = readdirSync(genDir).sort();
    } catch {
      return messages;
    }

    for (const gen of genEntries) {
      const genPath = join(genDir, gen);
      const files = [
        { file: "competitor_prompt.md", role: "user" as const },
        { file: "competitor_output.md", role: "assistant" as const },
        { file: "analyst.md", role: "assistant" as const },
        { file: "coach.md", role: "assistant" as const },
        { file: "trajectory.md", role: "system" as const },
      ];

      for (const { file, role } of files) {
        const filePath = join(genPath, file);
        if (existsSync(filePath)) {
          try {
            const content = readFileSync(filePath, "utf-8");
            if (content.trim()) {
              messages.push({ role, content, timestamp });
            }
          } catch { /* skip */ }
        }
      }
    }

    return messages;
  }
}

function emptyRedactionSummary(): RedactionSummary {
  return {
    totalDetections: 0,
    totalRedactions: 0,
    blocked: false,
    blockReasons: [],
    categoryCounts: {},
  };
}
