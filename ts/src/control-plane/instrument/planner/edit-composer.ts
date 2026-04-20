/**
 * A2-I Layer 5 — edit composer (spec §6.1).
 *
 * Orchestrates: safety filter → conflict detection → directive filter → import
 * planning → indentation matching → patch emission (unified-diff).
 *
 * Per-file composition order (spec §6.1):
 *   1. Safety filter — if sourceFile.hasSecretLiteral → refuse (surface
 *      SourceFile.secretMatches[0] in the refuse reason; Layer 3 concern).
 *   2. Conflict detection — delegate to conflict-detector.
 *   3. Directive filter — drop edits whose range falls inside `off`/`off-file`
 *      regions; if ALL dropped → refuse (`all-edits-dropped-by-directives`).
 *   4. Import planning — delegate to import-manager.
 *   5. Indentation matching — for each InsertStatementEdit, re-indent.
 *   6. Patch emission — apply edits RIGHT-TO-LEFT (descending byte offset) so
 *      earlier offsets stay valid. Generate unified-diff via Foundation B's
 *      `_shared/unified-diff-emitter.ts` (DRY).
 *
 * Import discipline (spec §3.3):
 *   - imports instrument/contract/, planner/ siblings, actuators/_shared (for
 *     unified-diff-emitter DRY reuse)
 *   - NO imports from instrument/scanner/, instrument/safety/, instrument/registry/
 */
import type { Patch } from "../../contract/types.js";
import { emitUnifiedDiff } from "../../actuators/_shared/unified-diff-emitter.js";
import type {
  EditDescriptor,
  ImportSpec,
  InsertStatementEdit,
  SecretMatch,
  SourceFile,
  SourceRange,
  DirectiveMap,
  DirectiveValue,
} from "../contract/plugin-interface.js";
import { detectConflicts, type ConflictReason } from "./conflict-detector.js";
import { planImports, type ImportPlan } from "./import-manager.js";
import { matchIndentation } from "./indentation-matcher.js";

export interface ComposeEditsOpts {
  readonly sourceFile: SourceFile;
  readonly edits: readonly EditDescriptor[];
}

export type RefusalReason =
  | { readonly kind: "secret-literal"; readonly match: SecretMatch; readonly message: string }
  | { readonly kind: "all-edits-dropped-by-directives" };

export interface ComposedEdit {
  readonly kind: EditDescriptor["kind"];
  readonly originalRange: SourceRange;
  readonly composedSource: string;
  readonly importContribution: readonly ImportSpec[];
}

export type ComposeResult =
  | { readonly kind: "patch"; readonly patch: Patch; readonly plan: readonly ComposedEdit[] }
  | { readonly kind: "refused"; readonly reason: RefusalReason; readonly diagnostics: readonly string[] }
  | { readonly kind: "conflict"; readonly reason: ConflictReason };

/**
 * Compose a set of edits into a single Patch for the given file, or refuse with
 * a structured reason.
 */
export function composeEdits(opts: ComposeEditsOpts): ComposeResult {
  const { sourceFile, edits } = opts;

  // 1. Safety filter (spec §6.1 + Layer 3 concern).
  if (sourceFile.hasSecretLiteral) {
    // Pick the FIRST match (lowest byteOffset) as the representative for the
    // refuse reason. The pr-body renderer (Layer 7) surfaces pattern + line.
    const match = sourceFile.secretMatches[0];
    if (match) {
      const message = formatSecretRefusalMessage(sourceFile.path, match);
      return {
        kind: "refused",
        reason: { kind: "secret-literal", match, message },
        diagnostics: [message],
      };
    }
    // Defensive: `hasSecretLiteral` is true but `secretMatches` is empty (stale
    // state). Fail closed.
    return {
      kind: "refused",
      reason: {
        kind: "secret-literal",
        match: { pattern: "unknown", byteOffset: 0, lineNumber: 0, excerpt: "" },
        message: `refusing to instrument ${sourceFile.path}: secret literal flag set without a recorded match`,
      },
      diagnostics: [`refusing to instrument ${sourceFile.path}: secret literal flag set`],
    };
  }

  // 2. Conflict detection.
  const conflictReport = detectConflicts(edits);
  if (conflictReport.kind === "conflict") {
    return { kind: "conflict", reason: conflictReport.reason };
  }
  const surviving = conflictReport.deduplicatedEdits;

  // 3. Directive filter.
  const afterDirectives = surviving.filter((e) => !editFallsInOffRegion(e, sourceFile.directives));
  if (surviving.length > 0 && afterDirectives.length === 0) {
    return {
      kind: "refused",
      reason: { kind: "all-edits-dropped-by-directives" },
      diagnostics: [`all edits for ${sourceFile.path} fell inside 'off' directive regions`],
    };
  }

  // 4. Import planning.
  const accumulatedImports: ImportSpec[] = [];
  for (const e of afterDirectives) {
    for (const spec of e.importsNeeded) accumulatedImports.push(spec);
  }
  const importPlan = planImports({ sourceFile, importsNeeded: accumulatedImports });

  // 5. Indentation matching — only for InsertStatementEdits.
  const composedList: ComposedEdit[] = [];
  for (const e of afterDirectives) {
    if (e.kind === "insert-statement") {
      const anchorLine = anchorLineOf(e);
      const composed = matchIndentation({
        sourceFile,
        anchorLine,
        rawStatement: e.statementSource,
      });
      composedList.push({
        kind: e.kind,
        originalRange: e.anchor.range,
        composedSource: composed,
        importContribution: e.importsNeeded,
      });
    } else {
      composedList.push({
        kind: e.kind,
        originalRange: e.range,
        composedSource: e.kind === "wrap-expression" ? renderWrap(e.range, e.wrapFn, e.wrapArgsBefore, e.wrapArgsAfter, sourceFile) : e.replacementSource,
        importContribution: e.importsNeeded,
      });
    }
  }

  // 6. Patch emission — apply RIGHT-TO-LEFT by byte offset.
  const applied = applyEditsRightToLeft(sourceFile, afterDirectives, importPlan);
  const patch = emitUnifiedDiff({
    filePath: sourceFile.path,
    oldContent: sourceFile.bytes.toString("utf-8"),
    newContent: applied,
  });

  return { kind: "patch", patch, plan: composedList };
}

// ---------------------------------------------------------------------------
// Safety message formatting (spec §5.4 error-message template)
// ---------------------------------------------------------------------------

function formatSecretRefusalMessage(path: string, match: SecretMatch): string {
  const prettyPattern = match.pattern
    .replace(/-/g, " ")
    .replace(/\b(\w)/g, (m) => m.toUpperCase());
  return `refusing to instrument ${path}: matched ${prettyPattern} pattern at line ${match.lineNumber}. Review and relocate secrets before re-running.`;
}

// ---------------------------------------------------------------------------
// Directive filtering
// ---------------------------------------------------------------------------

function editFallsInOffRegion(edit: EditDescriptor, directives: DirectiveMap): boolean {
  const range = edit.kind === "insert-statement" ? edit.anchor.range : edit.range;
  const startLine = range.startLineCol.line;
  const endLine = range.endLineCol.line;
  // Determine the effective directive state at every line from startLine to
  // endLine inclusive. Any line in 'off' (from `off` or unclosed `off-file`) →
  // edit is dropped.
  let state: DirectiveValue | "none" = "none";
  const maxLine = endLine;
  for (let line = 1; line <= maxLine; line += 1) {
    const dir = directives.get(line);
    if (dir === "off" || dir === "on") state = dir;
    else if (dir === "off-file" || dir === "on-file") state = dir;
    if (line >= startLine) {
      if (state === "off" || state === "off-file") return true;
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// Anchor line extraction (1-based)
// ---------------------------------------------------------------------------

function anchorLineOf(e: InsertStatementEdit): number {
  return e.anchor.kind === "before" ? e.anchor.range.startLineCol.line : e.anchor.range.endLineCol.line + 1;
}

// ---------------------------------------------------------------------------
// Render wrap-expression source using the original text from `sourceFile`
// ---------------------------------------------------------------------------

function renderWrap(
  range: SourceRange,
  wrapFn: string,
  before: readonly string[] | undefined,
  after: readonly string[] | undefined,
  sourceFile: SourceFile,
): string {
  const text = sourceFile.bytes.toString("utf-8");
  const inner = text.slice(range.startByte, range.endByte);
  const argsBefore = (before ?? []).join(", ");
  const argsAfter = (after ?? []).join(", ");
  const lead = argsBefore.length > 0 ? `${argsBefore}, ` : "";
  const trail = argsAfter.length > 0 ? `, ${argsAfter}` : "";
  return `${wrapFn}(${lead}${inner}${trail})`;
}

// ---------------------------------------------------------------------------
// Right-to-left edit application — the core correctness invariant
// ---------------------------------------------------------------------------

/**
 * Apply every surviving edit to the file bytes, PLUS the import-manager's
 * statement block, right-to-left by byte offset.
 *
 * "Right-to-left" means: sort edits by descending `startByte` and apply one at
 * a time. Because we never re-measure, earlier offsets remain valid throughout.
 * This is the critical correctness detail from the planner spec.
 */
function applyEditsRightToLeft(
  sourceFile: SourceFile,
  edits: readonly EditDescriptor[],
  importPlan: ImportPlan,
): string {
  const original = sourceFile.bytes.toString("utf-8");

  // Normalize every edit into a content-replace operation on [startByte, endByte).
  interface Op {
    readonly startByte: number;
    readonly endByte: number;
    readonly replacement: string;
    readonly tie: number; // stable tiebreaker when two ops share a boundary
  }
  const ops: Op[] = [];
  for (let i = 0; i < edits.length; i += 1) {
    const e = edits[i]!;
    if (e.kind === "wrap-expression") {
      const inner = original.slice(e.range.startByte, e.range.endByte);
      const lead = e.wrapArgsBefore && e.wrapArgsBefore.length > 0 ? `${e.wrapArgsBefore.join(", ")}, ` : "";
      const trail = e.wrapArgsAfter && e.wrapArgsAfter.length > 0 ? `, ${e.wrapArgsAfter.join(", ")}` : "";
      ops.push({
        startByte: e.range.startByte,
        endByte: e.range.endByte,
        replacement: `${e.wrapFn}(${lead}${inner}${trail})`,
        tie: i,
      });
    } else if (e.kind === "replace-expression") {
      ops.push({
        startByte: e.range.startByte,
        endByte: e.range.endByte,
        replacement: e.replacementSource,
        tie: i,
      });
    } else {
      // insert-statement
      const anchorLine = anchorLineOf(e);
      const reindented = matchIndentation({
        sourceFile,
        anchorLine,
        rawStatement: e.statementSource,
      });
      const insertByte =
        e.anchor.kind === "before" ? e.anchor.range.startByte : e.anchor.range.endByte;
      // Insertions have zero-width range [insertByte, insertByte) and are
      // distinguished from replacements by startByte === endByte.
      const payload =
        e.anchor.kind === "before" ? `${reindented}\n` : `\n${reindented}`;
      ops.push({
        startByte: insertByte,
        endByte: insertByte,
        replacement: payload,
        tie: i,
      });
    }
  }

  // Import block insertion: convert importPlan into a line-based insertion at
  // byte position. We compute byte offset of `insertAt.line` from the original.
  if (importPlan.statementSource.length > 0) {
    const offset = byteOffsetOfLine(original, importPlan.insertAt.line);
    ops.push({
      startByte: offset,
      endByte: offset,
      replacement: importPlan.statementSource,
      tie: edits.length,
    });
  }

  // Sort descending by startByte. Tiebreak: descending endByte (larger edits
  // first), then descending tie (later edits first so stable insertion order
  // holds when reversed).
  ops.sort((a, b) => {
    if (a.startByte !== b.startByte) return b.startByte - a.startByte;
    if (a.endByte !== b.endByte) return b.endByte - a.endByte;
    return b.tie - a.tie;
  });

  let text = original;
  for (const op of ops) {
    text = text.slice(0, op.startByte) + op.replacement + text.slice(op.endByte);
  }
  return text;
}

/** Compute the byte offset of the start of (1-based) `line` in `text`. Clamp to text length. */
function byteOffsetOfLine(text: string, line: number): number {
  if (line <= 1) return 0;
  let offset = 0;
  let current = 1;
  while (current < line && offset < text.length) {
    const nl = text.indexOf("\n", offset);
    if (nl === -1) return text.length;
    offset = nl + 1;
    current += 1;
  }
  return offset;
}
