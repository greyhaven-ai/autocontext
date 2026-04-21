/**
 * A2-I Layer 6 — pr-body.md renderer (spec §9.3).
 *
 * Pure function. No I/O. Matches the spec §9.3 template section-by-section so
 * the output is machine-parseable by downstream CI-review tooling.
 *
 * LLM enhancement discipline (spec §10):
 *   - Three narrative sites total: per-call-site rationale, per-file opt-out
 *     tips, session summary.
 *   - A2-I wires a static default for EACH site. Layer 8 replaces the
 *     `defaultRationale`/`defaultSummary` calls with LLM-enhanced variants
 *     when `enhancer.enhance(default, ctx)` returns non-null; on any failure
 *     the default is used silently.
 *   - This file contains TODO markers at each site for the Layer 8 hookup.
 *
 * Byte-determinism (spec §9.4):
 *   - `pr-body.md` is NOT byte-deterministic when LLM enhancement is enabled.
 *   - It IS byte-deterministic when `enhanced: false` (or no enhancer supplied)
 *     given the same inputs — relied on by the Layer 9 goldens.
 */
import { createHash } from "node:crypto";
import type { ContentHash } from "../../contract/branded-ids.js";
import type {
  EditDescriptor,
  InstrumentSession,
  InstrumentPlan,
  PluginAdvisory,
  SourceRange,
} from "../contract/plugin-interface.js";
import {
  enhance,
  RATIONALE_PROMPT,
  SESSION_SUMMARY_PROMPT,
  type EnhancerProvider,
  type EnhancerDiagnostic,
  type RationaleContext,
  type SessionSummaryContext,
} from "../llm/index.js";

/** Per-composed-edit projection (mirrors planner's ComposedEdit without importing it). */
export interface ComposedEditView {
  readonly kind: EditDescriptor["kind"];
  readonly originalRange: SourceRange;
  readonly composedSource: string;
}

export interface PrBodyInputs {
  readonly session: InstrumentSession;
  readonly plan: InstrumentPlan;
  readonly planHash: ContentHash;
  readonly detailedEdits: readonly PerFileDetailedEdits[];
  readonly filesSkipped: readonly SkippedFile[];
  readonly detectedUnchanged: readonly DetectedUnchanged[];
  readonly command: string;
  readonly nowIso: string;
  /**
   * Optional LLM enhancer wiring. When absent (or `enabled: false`), the
   * renderer falls back to the deterministic default templates — `pr-body.md`
   * stays byte-identical to pre-Layer-8 output, which is the property the
   * Layer 9 goldens rely on.
   */
  readonly enhancement?: {
    readonly enabled: boolean;
    readonly provider?: EnhancerProvider;
    readonly timeoutMs?: number;
    readonly onDiagnostic?: (d: EnhancerDiagnostic) => void;
  };
  /**
   * Plugin advisories collected during detection. When non-empty, the renderer
   * adds a "Refused with reason" section. When absent or empty, the section is
   * omitted so pre-advisory goldens remain byte-identical.
   */
  readonly advisories?: readonly PluginAdvisory[];
}

/** Per-file composed-edit metadata passed in from the orchestrator. */
export interface PerFileDetailedEdits {
  readonly filePath: string;
  readonly language: string;
  readonly sdkBreakdown: readonly { readonly sdkName: string; readonly count: number }[];
  readonly edits: readonly {
    readonly edit: EditDescriptor;
    readonly composed: ComposedEditView;
    readonly beforeSnippet: string;
    readonly afterSnippet: string;
  }[];
}

export interface SkippedFile {
  readonly filePath: string;
  readonly reason: string;
}

export interface DetectedUnchanged {
  readonly filePath: string;
  readonly pluginId: string;
  readonly reason: string;
}

/** Render the pr-body.md document. */
export async function renderPrBody(inputs: PrBodyInputs): Promise<string> {
  const parts: string[] = [];

  const filesAffected = inputs.detailedEdits.length;
  const callSitesWrapped = inputs.detailedEdits.reduce(
    (acc, f) => acc + f.edits.length,
    0,
  );

  const enhancementEnabled = inputs.enhancement?.enabled ?? false;
  const enhancementProvider = inputs.enhancement?.provider;
  const enhancementTimeoutMs = inputs.enhancement?.timeoutMs;
  const onDiagnostic = inputs.enhancement?.onDiagnostic;
  parts.push(
    `## Autocontext instrument — ${filesAffected} files affected, ${callSitesWrapped} call sites wrapped`,
  );
  parts.push("");
  parts.push(`Command: \`${inputs.command}\``);
  parts.push(
    `Session: \`${sessionUlidFromSession(inputs)}\` · Generated at \`${inputs.nowIso}\` by \`autocontext v${inputs.session.autoctxVersion}\``,
  );
  parts.push("");

  // Section: Summary by SDK
  // Spec §10.1 enhancement site 3 (session summary).
  parts.push("### Summary by SDK");
  const defaultSummaryText = defaultSummary(inputs);
  const summaryContext: SessionSummaryContext = {
    filesAffected,
    callSitesWrapped,
    filesSkipped: inputs.filesSkipped.length,
    skippedBySecretLiteral: inputs.filesSkipped.filter((f) =>
      /secret|pattern|AKIA|ghp_|sk-ant-|sk-|xox/i.test(f.reason),
    ).length,
    registeredPluginIds: (inputs.session.registeredPlugins ?? []).map((p) => p.id),
  };
  const summaryText = await enhance({
    defaultNarrative: defaultSummaryText,
    context: summaryContext,
    prompt: SESSION_SUMMARY_PROMPT,
    enabled: enhancementEnabled,
    provider: enhancementProvider,
    timeoutMs: enhancementTimeoutMs,
    onDiagnostic,
  });
  parts.push(summaryText);
  parts.push("");

  // Section: Files affected
  parts.push("### Files affected");
  if (inputs.detailedEdits.length === 0) {
    parts.push("_No files affected in this session._");
  } else {
    for (const f of inputs.detailedEdits) {
      parts.push(`#### \`${f.filePath}\` (+${f.edits.length} changes)`);
      for (const e of f.edits) {
        parts.push(`**Before:**\n\`\`\`${f.language}`);
        parts.push(e.beforeSnippet);
        parts.push("```");
        parts.push("**After:**");
        parts.push(`\`\`\`${f.language}`);
        parts.push(e.afterSnippet);
        parts.push("```");

        // Spec §10.1 enhancement site 1 (per-call-site rationale).
        const defaultRat = defaultRationale(f, e.edit);
        const rationaleLang = (f.language as RationaleContext["language"]);
        const ratCtx: RationaleContext = {
          filePath: f.filePath,
          language: rationaleLang,
          sdkName: f.sdkBreakdown[0]?.sdkName ?? "LLM client",
          beforeSnippet: e.beforeSnippet,
          afterSnippet: e.afterSnippet,
        };
        const rationaleText = await enhance({
          defaultNarrative: defaultRat,
          context: ratCtx,
          prompt: RATIONALE_PROMPT,
          enabled: enhancementEnabled,
          provider: enhancementProvider,
          timeoutMs: enhancementTimeoutMs,
          onDiagnostic,
        });
        parts.push(`*Rationale: ${rationaleText}*`);
        parts.push("");
      }
    }
  }

  // Section: Files skipped
  parts.push("### Files skipped");
  if (inputs.filesSkipped.length === 0) {
    parts.push("_No files skipped._");
  } else {
    parts.push("| Path | Reason |");
    parts.push("| --- | --- |");
    for (const s of inputs.filesSkipped) {
      parts.push(`| \`${s.filePath}\` | ${escapeTable(s.reason)} |`);
    }
  }
  parts.push("");

  // Section: Refused with reason (only present when advisories exist)
  const advisories = inputs.advisories ?? [];
  if (advisories.length > 0) {
    parts.push("### Refused with reason");
    const byKind = new Map<string, PluginAdvisory[]>();
    for (const adv of advisories) {
      const list = byKind.get(adv.kind) ?? [];
      list.push(adv);
      byKind.set(adv.kind, list);
    }
    const kinds = Array.from(byKind.keys()).sort();
    for (const kind of kinds) {
      parts.push(`#### ${kind}`);
      parts.push("| Path | Line | Plugin | Reason |");
      parts.push("| --- | --- | --- | --- |");
      for (const adv of byKind.get(kind)!) {
        const line = adv.range.startLineCol.line;
        parts.push(`| \`${adv.sourceFilePath}\` | ${line} | \`${adv.pluginId}\` | ${escapeTable(adv.reason)} |`);
      }
      parts.push("");
    }
  }

  // Section: Detected but unchanged
  parts.push("### Detected but unchanged");
  if (inputs.detectedUnchanged.length === 0) {
    parts.push("_No detections were filtered by safety / directives / opt-outs._");
  } else {
    parts.push("| Path | Plugin | Reason |");
    parts.push("| --- | --- | --- |");
    for (const u of inputs.detectedUnchanged) {
      parts.push(
        `| \`${u.filePath}\` | \`${u.pluginId}\` | ${escapeTable(u.reason)} |`,
      );
    }
  }
  parts.push("");

  // Section: How to apply
  parts.push("### How to apply");
  parts.push("```bash");
  parts.push("# Review the patches first:");
  parts.push(`ls .autocontext/instrument-patches/${sessionUlidFromSession(inputs)}/patches/`);
  parts.push("");
  parts.push("# Apply in-place (requires a clean working tree, or --force):");
  parts.push("autoctx instrument --apply");
  parts.push("");
  parts.push("# Or create a fresh branch + commit:");
  parts.push(
    "autoctx instrument --apply --branch autocontext-instrument --commit 'Instrument LLM clients'",
  );
  parts.push("```");
  parts.push("");

  // Section: How to opt out
  parts.push("### How to opt out");
  parts.push(
    "- Per-line: add `# autocontext: off` on the line **above** the client construction.",
  );
  parts.push(
    "- Per-file: add `# autocontext: off-file` near the top of the file (re-enable with `# autocontext: on-file`).",
  );
  parts.push("- Per-path: use `--exclude <glob>` or `--exclude-from <file>`.");
  parts.push("");

  // Section: Audit fingerprint
  parts.push("### Audit fingerprint");
  parts.push(`- Session: \`${sessionUlidFromSession(inputs)}\``);
  parts.push(`- Session-plan hash: \`${inputs.planHash}\` (of \`plan.json\`)`);
  parts.push(`- Autoctx version: \`${inputs.session.autoctxVersion}\``);
  const registered = inputs.session.registeredPlugins
    .map((p) => `${p.id}@${p.version}`)
    .join(", ");
  parts.push(`- Registered plugins: \`${registered || "<none>"}\``);
  parts.push(`- \`.gitignore\` rev: \`${inputs.session.gitignoreFingerprint}\``);
  parts.push("");

  return parts.join("\n");
}

// ---------------------------------------------------------------------------
// Default narrative templates (the three LLM enhancement sites)
// ---------------------------------------------------------------------------

/**
 * Default single-paragraph session summary. Grouping by SDK keeps readers
 * oriented when multiple plugins ran in one invocation.
 *
 * TODO(A2-I Layer 8): replace with `enhancer.enhance(defaultSummary(ctx), ctx)`.
 */
function defaultSummary(inputs: PrBodyInputs): string {
  const sdkCounts = new Map<string, number>();
  for (const f of inputs.detailedEdits) {
    for (const sb of f.sdkBreakdown) {
      sdkCounts.set(sb.sdkName, (sdkCounts.get(sb.sdkName) ?? 0) + sb.count);
    }
  }
  if (sdkCounts.size === 0) {
    return "This session produced no instrumentation changes.";
  }
  const entries = Array.from(sdkCounts.entries()).sort(([a], [b]) => (a < b ? -1 : 1));
  const lines = entries.map(([sdk, n]) => `- **${sdk}**: ${n} call site${n === 1 ? "" : "s"} wrapped`);
  return lines.join("\n");
}

/**
 * Default per-call-site rationale. Spec §10.4 says narrative explains what
 * the change does + why; the default is terse-but-accurate.
 *
 * TODO(A2-I Layer 8): replace with `enhancer.enhance(defaultRationale(ctx), ctx)`.
 */
function defaultRationale(file: PerFileDetailedEdits, edit: EditDescriptor): string {
  const sdk = file.sdkBreakdown[0]?.sdkName ?? "LLM client";
  if (edit.kind === "wrap-expression") {
    return (
      `Wraps the ${sdk} client construction with \`${edit.wrapFn}(...)\` so every ` +
      `call through this client emits an Autocontext trace.`
    );
  }
  if (edit.kind === "insert-statement") {
    return `Inserts an Autocontext setup statement near the ${sdk} client.`;
  }
  return `Replaces a ${sdk} expression with an Autocontext-instrumented equivalent.`;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function escapeTable(s: string): string {
  return s.replace(/\|/g, "\\|");
}

function sessionUlidFromSession(inputs: PrBodyInputs): string {
  return extractUlidFromCommand(inputs.command) ?? "<session-ulid>";
}

/** Extract a ULID-like token from the command string, or null. */
function extractUlidFromCommand(command: string): string | null {
  const m = command.match(/session=([0-9A-HJKMNP-TV-Z]{26})/);
  return m ? m[1]! : null;
}

/**
 * Content-address a string: sha256 over its UTF-8 bytes. Used by the
 * orchestrator to compute the plan hash; exported here for symmetry so
 * downstream callers don't need to re-implement the branded-hash format.
 */
export function sha256ContentHash(s: string): ContentHash {
  const h = createHash("sha256").update(s, "utf-8").digest("hex");
  return `sha256:${h}` as ContentHash;
}
