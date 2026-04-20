/**
 * A2-I Layer 6 — pipeline orchestrator (spec §7.1 + §7.3 + §7.4 + §7.5).
 *
 * End-to-end flow per spec §7:
 *   1. Preflight (preflight.ts) — short-circuit on first failure with the
 *      spec-mandated exit code.
 *   2. Scan (scanner.scanRepo) — yield `SourceFile` instances.
 *   3. Detect — run each file through every plugin registered for its language,
 *      running the plugin's tree-sitter queries + `plugin.produce()`.
 *      The A2-I bundle registers zero plugins by default (§2.1), so a bare
 *      `runInstrument` call produces zero edits unless the caller has first
 *      invoked `registerDetectorPlugin()`.
 *   4. Compose (planner.composeEdits) — per file, translate EditDescriptor[]
 *      into a Patch. Handle `refused`/`conflict`/`patch` discriminators.
 *   5. Emit session directory (always — every mode writes session.json,
 *      detections.jsonl, plan.json, patches/, pr-body.md).
 *   6. Apply if requested — write afterContent to the working tree.
 *   7. Branch + commit if `apply-branch` — `git checkout -b / git add / git commit`.
 *
 * Determinism contract (spec §9.4):
 *   - `plan.json` is byte-deterministic given the same `(cwd-snapshot, flags,
 *     nowIso, sessionUlid, plugin registry)`.
 *   - `session.json` is NOT byte-deterministic (contains ULID + timestamps)
 *     but IS byte-deterministic given the same INJECTED ULID + nowIso.
 *   - `pr-body.md` is byte-deterministic when `enhanced: false`.
 *
 * Import discipline (spec §3.3):
 *   - This module is the ONLY point that imports from EVERY instrument
 *     sub-context (contract/, scanner/, safety/, registry/, planner/).
 *     Individual helpers below do not leak that reach to the rest of
 *     pipeline/ — `modes/*.ts` and `preflight.ts` are each narrow.
 */
import { createHash } from "node:crypto";
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { parseContentHash, canonicalJsonStringify, type ContentHash } from "../../contract/index.js";
import { scanRepo } from "../scanner/walker.js";
import { pluginsForLanguage } from "../registry/plugin-registry.js";
import { composeEdits, type ComposeResult, type ComposedEdit } from "../planner/edit-composer.js";
import type { ConflictReason } from "../planner/conflict-detector.js";
import type {
  ConflictDecision,
  DetectorPlugin,
  EditDescriptor,
  InstrumentFlagsSnapshot,
  InstrumentLanguage,
  InstrumentPlan,
  InstrumentSession,
  PlanSourceFileMetadata,
  SafetyDecision,
  SourceFile,
  TreeSitterMatch,
} from "../contract/plugin-interface.js";
import {
  checkBranchPreconditions,
  checkCwdReadable,
  checkExcludeFromReadable,
  checkRegistryPopulated,
  checkWorkingTreeClean,
  type GitDetector,
  type PreflightVerdict,
} from "./preflight.js";
import { runDryRunMode } from "./modes/dry-run.js";
import { runApplyMode } from "./modes/apply.js";
import { runBranchMode, type BranchGitExecutor } from "./modes/branch.js";
import {
  renderPrBody,
  type DetectedUnchanged,
  type PerFileDetailedEdits,
} from "./pr-body-renderer.js";

// ---------------------------------------------------------------------------
// Public surface
// ---------------------------------------------------------------------------

export type InstrumentMode = "dry-run" | "apply" | "apply-branch";

export interface InstrumentInputs {
  readonly cwd: string;
  readonly mode: InstrumentMode;
  readonly branchName?: string;
  readonly commitMessage?: string;
  readonly exclude?: readonly string[];
  readonly excludeFrom?: string;
  readonly maxFileBytes?: number;
  readonly failIfEmpty?: boolean;
  readonly force?: boolean;
  readonly enhanced?: boolean;
  /** INJECTED clock for deterministic testing. Production supplies Date.now(). */
  readonly nowIso: string;
  /** INJECTED ULID for deterministic testing. Production supplies `ulid()`. */
  readonly sessionUlid: string;
  /** Autoctx version string to embed in session metadata. */
  readonly autoctxVersion?: string;
  /** Advanced: dependency-injected git detector used by preflight + branch mode. */
  readonly gitDetector?: GitDetector;
  /** Advanced: branch-mode git executor (injected in tests to avoid real git subprocesses). */
  readonly branchExecutor?: BranchGitExecutor;
  /** Advanced: registered-plugin info for session.json (otherwise collected from the live registry). */
  readonly registeredPluginsOverride?: InstrumentSession["registeredPlugins"];
  /** Advanced: skip writing the session directory (for in-process unit tests of the pipeline). */
  readonly skipSessionDirWrite?: boolean;
  /**
   * Advanced: injected LLM provider for `enhanced` mode. Tests pass a mock
   * here; production either leaves this undefined (in which case enhancement
   * is effectively disabled even with `enhanced: true`) or future layers
   * could wire a real provider via the existing `providers/` factory.
   */
  readonly enhancementProvider?: import("../llm/index.js").EnhancerProvider;
}

export interface InstrumentResult {
  readonly sessionDir: string;
  readonly sessionUlid: string;
  readonly mode: InstrumentMode;
  readonly filesScanned: number;
  readonly filesAffected: number;
  readonly callSitesDetected: number;
  readonly filesSkipped: readonly { readonly path: string; readonly reason: string }[];
  readonly conflicts: readonly ConflictReason[];
  readonly applyResult?: {
    readonly filesWritten: readonly string[];
    readonly commitSha?: string;
    readonly branchName?: string;
  };
  readonly exitCode: number;
  /** Human-readable one-line summary. */
  readonly summary: string;
  /** Plan-hash (sha256 of canonical plan.json). Useful for CI drift-detection. */
  readonly planHash: ContentHash;
}

/**
 * Entry point. Runs the full A2-I pipeline for the given inputs and mode.
 * Never throws for expected domain failures — maps them to `exitCode` instead.
 */
export async function runInstrument(opts: InstrumentInputs): Promise<InstrumentResult> {
  const version = opts.autoctxVersion ?? "0.0.0-dev";
  const sessionDir = sessionDirPath(opts.cwd, opts.sessionUlid);

  // -------------------------------------------------------------------------
  // 1. Preflight (every failure is a hard exit — spec §7.2 short-circuits).
  // -------------------------------------------------------------------------
  const cwdCheck = checkCwdReadable(opts.cwd);
  if (!cwdCheck.ok) return earlyExit(opts, sessionDir, cwdCheck);

  const efCheck = checkExcludeFromReadable(opts.excludeFrom);
  if (!efCheck.ok) return earlyExit(opts, sessionDir, efCheck);

  const regCheck = checkRegistryPopulated(opts.failIfEmpty === true);
  if (!regCheck.ok) return earlyExit(opts, sessionDir, regCheck);

  if (opts.mode === "apply-branch") {
    const branchCheck = checkBranchPreconditions({
      cwd: opts.cwd,
      ...(opts.gitDetector ? { detector: opts.gitDetector } : {}),
    });
    if (!branchCheck.ok) return earlyExit(opts, sessionDir, branchCheck);
  }

  // -------------------------------------------------------------------------
  // 2. Scan.
  // -------------------------------------------------------------------------
  const sourceFiles: SourceFile[] = [];
  const oversized: { path: string; sizeBytes: number }[] = [];
  for await (const sf of scanRepo({
    cwd: opts.cwd,
    ...(opts.exclude ? { extraExcludes: opts.exclude } : {}),
    ...(opts.excludeFrom ? { excludeFrom: opts.excludeFrom } : {}),
    ...(opts.maxFileBytes !== undefined ? { maxFileBytes: opts.maxFileBytes } : {}),
    onSkipOversized: (p, sz) => oversized.push({ path: p, sizeBytes: sz }),
  })) {
    sourceFiles.push(sf);
  }

  // -------------------------------------------------------------------------
  // 3. Detect.
  // -------------------------------------------------------------------------
  const detections: Detection[] = [];
  const editsByFile = new Map<string, EditDescriptor[]>();
  for (const sf of sourceFiles) {
    const plugins = pluginsForLanguage(sf.language);
    if (plugins.length === 0) continue;
    for (const plugin of plugins) {
      const matches = runPluginQueries(sf, plugin);
      for (const match of matches) {
        const produced = plugin.produce(match, sf);
        const editsWithMeta: EditDescriptor[] = produced.map((e) => injectPluginMeta(e, plugin.id, sf.path));
        detections.push({
          pluginId: plugin.id,
          filePath: sf.path,
          matchRange: firstCaptureRange(match),
          editsProduced: editsWithMeta.length,
        });
        const list = editsByFile.get(sf.path) ?? [];
        list.push(...editsWithMeta);
        editsByFile.set(sf.path, list);
      }
    }
  }

  // -------------------------------------------------------------------------
  // 4. Compose per file.
  // -------------------------------------------------------------------------
  const composedByFile = new Map<string, ComposeResult>();
  const filesSkipped: { path: string; reason: string }[] = [];
  const detectedUnchanged: DetectedUnchanged[] = [];
  const conflicts: ConflictReason[] = [];
  let partialSuccessAdvisory = false;

  const filesByPath = new Map<string, SourceFile>();
  for (const sf of sourceFiles) filesByPath.set(sf.path, sf);

  for (const [filePath, edits] of editsByFile) {
    const sf = filesByPath.get(filePath);
    if (!sf) continue; // defensive
    const result = composeEdits({ sourceFile: sf, edits });
    composedByFile.set(filePath, result);
    if (result.kind === "refused") {
      partialSuccessAdvisory = true;
      filesSkipped.push({
        path: filePath,
        reason: refusalReasonText(result.reason),
      });
      for (const e of edits) {
        detectedUnchanged.push({
          filePath,
          pluginId: e.pluginId,
          reason: refusalReasonText(result.reason),
        });
      }
    } else if (result.kind === "conflict") {
      conflicts.push(result.reason);
    }
  }

  // If any conflict arose, fail fast with exit 13 after still producing the
  // session dir (so developers can inspect the conflict artifact).
  const conflictHappened = conflicts.length > 0;

  // -------------------------------------------------------------------------
  // 5. Compose plan.json + session.json.
  // -------------------------------------------------------------------------
  const registeredPlugins = opts.registeredPluginsOverride ?? collectRegisteredPluginsSnapshot();
  const flagsSnapshot = buildFlagsSnapshot(opts);
  const gitignoreFingerprint = computeGitignoreFingerprint(opts.cwd);

  const session: InstrumentSession = {
    cwd: opts.cwd,
    flags: flagsSnapshot,
    startedAt: opts.nowIso,
    endedAt: opts.nowIso,
    autoctxVersion: version,
    registeredPlugins,
    gitignoreFingerprint,
  };

  const plan = buildPlan(sourceFiles, composedByFile, editsByFile);
  const planJson = canonicalJsonStringify(plan as unknown);
  const planHash = sha256Hash(planJson);

  // -------------------------------------------------------------------------
  // 6. Compose detailedEdits for pr-body + build skipped-file list.
  // -------------------------------------------------------------------------
  for (const o of oversized) {
    filesSkipped.push({ path: o.path, reason: `oversized (${o.sizeBytes} bytes)` });
  }

  const detailedEdits = buildDetailedEdits(composedByFile, editsByFile, filesByPath, registeredPlugins);

  const command = buildCommandLine(opts);
  const prBody = await renderPrBody({
    session,
    plan,
    planHash,
    detailedEdits,
    // pr-body speaks in `filePath`; orchestrator + InstrumentResult use `path`
    // (legacy Foundation B vocabulary). Project once at the renderer boundary.
    filesSkipped: filesSkipped.map((f) => ({ filePath: f.path, reason: f.reason })),
    detectedUnchanged,
    command: `${command} session=${opts.sessionUlid}`,
    nowIso: opts.nowIso,
    enhancement: opts.enhanced
      ? {
          enabled: true,
          provider: opts.enhancementProvider,
        }
      : undefined,
  });

  // -------------------------------------------------------------------------
  // 7. Write session directory (always — every mode writes it).
  // -------------------------------------------------------------------------
  const affectedPatches: { filePath: string; patch: string }[] = [];
  for (const [filePath, result] of composedByFile) {
    if (result.kind === "patch") {
      affectedPatches.push({ filePath, patch: result.patch.unifiedDiff });
    }
  }

  if (opts.skipSessionDirWrite !== true) {
    runDryRunMode({
      sessionDir,
      session,
      plan,
      planJson,
      detections,
      patches: affectedPatches,
      prBody,
    });
  }

  // -------------------------------------------------------------------------
  // 8. Apply / apply-branch.
  // -------------------------------------------------------------------------
  let applyResult: InstrumentResult["applyResult"] | undefined = undefined;
  let exitCode = 0;
  let summary = "";

  if (conflictHappened) {
    exitCode = 13;
    summary = `Plugin conflict — ${conflicts.length} conflict(s) blocked the session.`;
  } else if (opts.mode === "apply" || opts.mode === "apply-branch") {
    // Clean-tree preflight only now that we know the target paths.
    const targetPaths = affectedPatches.map((p) => p.filePath);
    const cleanCheck = checkWorkingTreeClean({
      cwd: opts.cwd,
      paths: targetPaths,
      force: opts.force === true,
      ...(opts.gitDetector ? { detector: opts.gitDetector } : {}),
    });
    if (!cleanCheck.ok) {
      return {
        sessionDir,
        sessionUlid: opts.sessionUlid,
        mode: opts.mode,
        filesScanned: sourceFiles.length,
        filesAffected: affectedPatches.length,
        callSitesDetected: detections.reduce((a, d) => a + d.editsProduced, 0),
        filesSkipped,
        conflicts,
        exitCode: cleanCheck.exitCode,
        summary: cleanCheck.message,
        planHash,
      };
    }

    const patches: { filePath: string; afterContent: string }[] = [];
    for (const [filePath, result] of composedByFile) {
      if (result.kind === "patch" && result.patch.afterContent !== undefined) {
        patches.push({ filePath, afterContent: result.patch.afterContent });
      }
    }

    if (opts.mode === "apply") {
      const res = runApplyMode({
        cwd: opts.cwd,
        sessionDir,
        patches,
        sessionUlid: opts.sessionUlid,
        nowIso: opts.nowIso,
      });
      applyResult = { filesWritten: res.filesWritten };
    } else {
      const res = runBranchMode({
        cwd: opts.cwd,
        sessionDir,
        patches,
        branchName: opts.branchName ?? defaultBranchName(opts.nowIso),
        commitMessage: opts.commitMessage ?? `Instrument LLM clients (autocontext v${version})`,
        sessionUlid: opts.sessionUlid,
        nowIso: opts.nowIso,
        ...(opts.gitDetector ? { detector: opts.gitDetector } : {}),
        ...(opts.branchExecutor ? { executor: opts.branchExecutor } : {}),
      });
      applyResult = {
        filesWritten: res.filesWritten,
        ...(res.commitSha !== undefined ? { commitSha: res.commitSha } : {}),
        branchName: res.branchName,
      };
    }

    if (partialSuccessAdvisory) {
      exitCode = 2;
      summary = `Applied ${affectedPatches.length} file(s); ${filesSkipped.length} skipped (advisory).`;
    } else {
      summary = `Applied ${affectedPatches.length} file(s).`;
    }
  } else {
    // dry-run
    if (partialSuccessAdvisory && affectedPatches.length === 0 && filesSkipped.length > 0) {
      exitCode = 2;
      summary = `Dry-run produced 0 patches; ${filesSkipped.length} file(s) skipped.`;
    } else if (partialSuccessAdvisory) {
      exitCode = 2;
      summary = `Dry-run produced ${affectedPatches.length} patch(es); ${filesSkipped.length} skipped (advisory).`;
    } else {
      summary = `Dry-run produced ${affectedPatches.length} patch(es).`;
    }
  }

  return {
    sessionDir,
    sessionUlid: opts.sessionUlid,
    mode: opts.mode,
    filesScanned: sourceFiles.length,
    filesAffected: affectedPatches.length,
    callSitesDetected: detections.reduce((a, d) => a + d.editsProduced, 0),
    filesSkipped,
    conflicts,
    ...(applyResult ? { applyResult } : {}),
    exitCode,
    summary,
    planHash,
  };
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

interface Detection {
  readonly pluginId: string;
  readonly filePath: string;
  readonly matchRange: { readonly startByte: number; readonly endByte: number };
  readonly editsProduced: number;
}

function sessionDirPath(cwd: string, sessionUlid: string): string {
  return join(cwd, ".autocontext", "instrument-patches", sessionUlid);
}

function defaultBranchName(nowIso: string): string {
  // Extract YYYYMMDD from the ISO timestamp.
  const m = nowIso.match(/^(\d{4})-(\d{2})-(\d{2})/);
  const stamp = m ? `${m[1]}${m[2]}${m[3]}` : "00000000";
  return `autocontext-instrument-${stamp}`;
}

function earlyExit(
  opts: InstrumentInputs,
  sessionDir: string,
  verdict: Extract<PreflightVerdict, { ok: false }>,
): InstrumentResult {
  const planStub = canonicalJsonStringify({
    schemaVersion: "1.0",
    edits: [],
    sourceFiles: [],
    conflictDecisions: [],
    safetyDecisions: [],
  });
  return {
    sessionDir,
    sessionUlid: opts.sessionUlid,
    mode: opts.mode,
    filesScanned: 0,
    filesAffected: 0,
    callSitesDetected: 0,
    filesSkipped: [],
    conflicts: [],
    exitCode: verdict.exitCode,
    summary: verdict.message,
    planHash: sha256Hash(planStub),
  };
}

/**
 * A2-I ships with zero tree-sitter query glue. `plugin.treeSitterQueries` is a
 * list of .scm query strings; in A2-I the pipeline runs the queries via a
 * minimal synthesized match. Real plugins (A2-II+) may implement their own
 * query-running via `sourceFile.tree` inside `produce()`.
 *
 * For A2-I we use the simplest correct contract: when `treeSitterQueries` is
 * empty we never call `produce()`. When non-empty, we call `produce()` ONCE
 * per query with a synthesized empty `TreeSitterMatch`. This defers full
 * QueryCursor wiring to A2-II (where the first real plugin lands) while
 * keeping the pipeline honest: every registered plugin sees every scanned file.
 */
function runPluginQueries(
  sourceFile: SourceFile,
  plugin: DetectorPlugin,
): readonly TreeSitterMatch[] {
  if (plugin.treeSitterQueries.length === 0) return [];
  // Invoke tree property once so plugins that use `sourceFile.tree` inside
  // `produce()` see the lazily-parsed CST without having to request it first.
  void sourceFile.tree;
  return plugin.treeSitterQueries.map(() => ({ captures: [] }));
}

function firstCaptureRange(match: TreeSitterMatch): { startByte: number; endByte: number } {
  if (match.captures.length === 0) return { startByte: 0, endByte: 0 };
  const n = match.captures[0]!.node;
  return { startByte: n.startIndex, endByte: n.endIndex };
}

function injectPluginMeta(edit: EditDescriptor, pluginId: string, sourceFilePath: string): EditDescriptor {
  if (edit.kind === "wrap-expression") return { ...edit, pluginId, sourceFilePath };
  if (edit.kind === "insert-statement") return { ...edit, pluginId, sourceFilePath };
  return { ...edit, pluginId, sourceFilePath };
}

function buildFlagsSnapshot(opts: InstrumentInputs): InstrumentFlagsSnapshot {
  const base: InstrumentFlagsSnapshot = {
    mode: opts.mode,
    enhanced: opts.enhanced === true,
    maxFileBytes: opts.maxFileBytes ?? 1_048_576,
    failIfEmpty: opts.failIfEmpty === true,
    excludes: opts.exclude ?? [],
    output: "pretty",
    force: opts.force === true,
  };
  const withOptional: InstrumentFlagsSnapshot = {
    ...base,
    ...(opts.branchName ? { branch: opts.branchName } : {}),
    ...(opts.commitMessage ? { commit: opts.commitMessage } : {}),
    ...(opts.excludeFrom ? { excludeFrom: opts.excludeFrom } : {}),
  };
  return withOptional;
}

function collectRegisteredPluginsSnapshot(): InstrumentSession["registeredPlugins"] {
  const langs: readonly InstrumentLanguage[] = [
    "python",
    "typescript",
    "javascript",
    "jsx",
    "tsx",
  ];
  const seen = new Set<string>();
  const out: { id: string; version: string; sdkName: string; language: InstrumentLanguage }[] = [];
  for (const l of langs) {
    for (const p of pluginsForLanguage(l)) {
      if (seen.has(p.id)) continue;
      seen.add(p.id);
      out.push({
        id: p.id,
        version: "0.0.0",
        sdkName: p.supports.sdkName,
        language: p.supports.language,
      });
    }
  }
  return out;
}

function computeGitignoreFingerprint(cwd: string): ContentHash {
  const gi = join(cwd, ".gitignore");
  let contents = "";
  if (existsSync(gi)) {
    try {
      contents = readFileSync(gi, "utf-8");
    } catch {
      contents = "";
    }
  }
  return sha256Hash(contents);
}

function sha256Hash(content: string): ContentHash {
  const hex = createHash("sha256").update(content, "utf-8").digest("hex");
  const candidate = `sha256:${hex}`;
  const parsed = parseContentHash(candidate);
  if (parsed === null) {
    throw new Error(`sha256Hash: produced non-matching digest: ${candidate}`);
  }
  return parsed;
}

function buildPlan(
  sourceFiles: readonly SourceFile[],
  composedByFile: ReadonlyMap<string, ComposeResult>,
  editsByFile: ReadonlyMap<string, readonly EditDescriptor[]>,
): InstrumentPlan {
  // Sort files by path for deterministic output.
  const sortedFiles = sourceFiles.slice().sort((a, b) => (a.path < b.path ? -1 : 1));

  const edits: EditDescriptor[] = [];
  const metaList: PlanSourceFileMetadata[] = [];
  const conflictDecisions: { filePath: string; decision: ConflictDecision }[] = [];
  const safetyDecisions: { filePath: string; decision: SafetyDecision }[] = [];

  for (const sf of sortedFiles) {
    const fileEdits = editsByFile.get(sf.path);
    if (fileEdits) {
      edits.push(...fileEdits);
    }
    metaList.push(toPlanMeta(sf));
    const composed = composedByFile.get(sf.path);
    if (composed) {
      if (composed.kind === "patch") {
        conflictDecisions.push({ filePath: sf.path, decision: { kind: "accepted" } });
        safetyDecisions.push({ filePath: sf.path, decision: { kind: "allow" } });
      } else if (composed.kind === "refused") {
        conflictDecisions.push({ filePath: sf.path, decision: { kind: "accepted" } });
        safetyDecisions.push({
          filePath: sf.path,
          decision: { kind: "refuse", reason: refusalReasonText(composed.reason) },
        });
      } else {
        const ids = conflictPluginIds(composed.reason);
        conflictDecisions.push({
          filePath: sf.path,
          decision: {
            kind: "rejected-conflict",
            conflictingPluginIds: ids,
            reason: conflictReasonText(composed.reason),
          },
        });
      }
    }
  }

  return {
    schemaVersion: "1.0",
    edits,
    sourceFiles: metaList,
    conflictDecisions,
    safetyDecisions,
  };
}

function toPlanMeta(sf: SourceFile): PlanSourceFileMetadata {
  const offLines: number[] = [];
  let offFileAtLine: number | undefined = undefined;
  for (const [line, val] of sf.directives) {
    if (val === "off") offLines.push(line);
    if (val === "off-file" && offFileAtLine === undefined) offFileAtLine = line;
  }
  offLines.sort((a, b) => a - b);
  const existing: { module: string; names: readonly string[] }[] = [];
  for (const ei of sf.existingImports) {
    existing.push({ module: ei.module, names: Array.from(ei.names).sort() });
  }
  existing.sort((a, b) => (a.module < b.module ? -1 : 1));
  const metadata: PlanSourceFileMetadata = {
    path: sf.path,
    language: sf.language,
    directivesSummary: {
      offLines,
      ...(offFileAtLine !== undefined ? { offFileAtLine } : {}),
    },
    hasSecretLiteral: sf.hasSecretLiteral,
    existingImports: existing,
  };
  return metadata;
}

function refusalReasonText(r: Extract<ComposeResult, { kind: "refused" }>["reason"]): string {
  if (r.kind === "secret-literal") return r.message;
  return "all edits dropped by off directives";
}

function conflictReasonText(r: ConflictReason): string {
  switch (r.kind) {
    case "overlapping-ranges":
      return `overlapping edit ranges between plugins ${r.editA.pluginId} and ${r.editB.pluginId}`;
    case "insert-anchor-inside-another-edit":
      return `insert-statement anchor from ${r.insertEdit.pluginId} lands inside ${r.containingEdit.pluginId} edit range`;
    case "same-range-different-wrapfn":
      return `plugins ${r.editA.pluginId} and ${r.editB.pluginId} wrap the same range with different wrapFn (${r.editA.wrapFn} vs ${r.editB.wrapFn})`;
  }
}

function conflictPluginIds(r: ConflictReason): readonly string[] {
  if (r.kind === "overlapping-ranges") return [r.editA.pluginId, r.editB.pluginId];
  if (r.kind === "insert-anchor-inside-another-edit") return [r.insertEdit.pluginId, r.containingEdit.pluginId];
  return [r.editA.pluginId, r.editB.pluginId];
}

/** Mutable shape of one per-file detailed edit entry during construction. */
interface DetailedEditEntry {
  readonly edit: EditDescriptor;
  readonly composed: { kind: EditDescriptor["kind"]; originalRange: ComposedEdit["originalRange"]; composedSource: string };
  readonly beforeSnippet: string;
  readonly afterSnippet: string;
}

function buildDetailedEdits(
  composedByFile: ReadonlyMap<string, ComposeResult>,
  editsByFile: ReadonlyMap<string, readonly EditDescriptor[]>,
  filesByPath: ReadonlyMap<string, SourceFile>,
  registeredPlugins: InstrumentSession["registeredPlugins"],
): PerFileDetailedEdits[] {
  const pluginToSdk = new Map<string, string>();
  for (const p of registeredPlugins) pluginToSdk.set(p.id, p.sdkName);

  const detailed: PerFileDetailedEdits[] = [];
  const keys = Array.from(composedByFile.keys()).sort();
  for (const filePath of keys) {
    const result = composedByFile.get(filePath)!;
    if (result.kind !== "patch") continue;
    const sf = filesByPath.get(filePath);
    if (!sf) continue;
    const fileEdits = editsByFile.get(filePath) ?? [];
    const sdkCounts = new Map<string, number>();
    for (const e of fileEdits) {
      const sdk = pluginToSdk.get(e.pluginId) ?? "unknown";
      sdkCounts.set(sdk, (sdkCounts.get(sdk) ?? 0) + 1);
    }
    const sdkBreakdown = Array.from(sdkCounts.entries())
      .sort(([a], [b]) => (a < b ? -1 : 1))
      .map(([sdkName, count]) => ({ sdkName, count }));
    const edits: DetailedEditEntry[] = [];
    for (let i = 0; i < fileEdits.length && i < result.plan.length; i += 1) {
      const e = fileEdits[i]!;
      const composed: ComposedEdit = result.plan[i]!;
      edits.push({
        edit: e,
        composed: {
          kind: composed.kind,
          originalRange: composed.originalRange,
          composedSource: composed.composedSource,
        },
        beforeSnippet: extractSnippet(sf.bytes.toString("utf-8"), composed.originalRange.startByte, composed.originalRange.endByte),
        afterSnippet: composed.composedSource,
      });
    }
    detailed.push({
      filePath,
      language: sf.language,
      sdkBreakdown,
      edits,
    });
  }
  return detailed;
}

function extractSnippet(text: string, startByte: number, endByte: number): string {
  const s = Math.max(0, startByte);
  const e = Math.min(text.length, endByte);
  return text.slice(s, e);
}

function buildCommandLine(opts: InstrumentInputs): string {
  const parts = ["autoctx instrument"];
  if (opts.mode === "dry-run") parts.push("--dry-run");
  if (opts.mode === "apply") parts.push("--apply");
  if (opts.mode === "apply-branch") {
    parts.push("--apply");
    if (opts.branchName) parts.push(`--branch ${opts.branchName}`);
    if (opts.commitMessage) parts.push(`--commit '${opts.commitMessage}'`);
  }
  for (const g of opts.exclude ?? []) parts.push(`--exclude ${g}`);
  if (opts.excludeFrom) parts.push(`--exclude-from ${opts.excludeFrom}`);
  if (opts.failIfEmpty === true) parts.push("--fail-if-empty");
  if (opts.force === true) parts.push("--force");
  if (opts.enhanced === true) parts.push("--enhanced");
  if (opts.maxFileBytes !== undefined) parts.push(`--max-file-bytes ${opts.maxFileBytes}`);
  return parts.join(" ");
}

// Re-export for test usage.
export type { ConflictReason } from "../planner/conflict-detector.js";
