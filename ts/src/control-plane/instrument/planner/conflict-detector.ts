/**
 * A2-I Layer 5 — conflict detector (spec §6.4).
 *
 * Vocabulary (verbatim from spec §6):
 *   - EditDescriptor — WrapExpressionEdit | InsertStatementEdit | ReplaceExpressionEdit
 *   - conflict — overlapping ranges, insert-anchor-inside-edit, same-range-different-wrapFn
 *   - duplicate — same range + same kind + same wrapFn (deduplicated silently)
 *
 * Pure byte-range arithmetic; no casts to any/unknown.
 *
 * Half-open convention: [startByte, endByte). Two ranges overlap iff
 * `a.start < b.end && b.start < a.end`. With zero-width ranges (startByte ==
 * endByte) we treat overlap as NOT present for the same-point case — pure
 * insertions at the same byte are legal (two insert-statements at the same
 * anchor both compose).
 *
 * Import discipline (spec §3.3):
 *   - imports from instrument/contract/ ONLY
 *   - NO imports from sibling planner modules (conflict-detector is the lowest
 *     Layer 5 primitive; other planner modules may import from here)
 */
import type {
  EditDescriptor,
  SourceRange,
  WrapExpressionEdit,
  InsertStatementEdit,
} from "../contract/plugin-interface.js";

export type ConflictReason =
  | { readonly kind: "overlapping-ranges"; readonly editA: EditDescriptor; readonly editB: EditDescriptor }
  | {
      readonly kind: "insert-anchor-inside-another-edit";
      readonly insertEdit: InsertStatementEdit;
      readonly containingEdit: EditDescriptor;
    }
  | {
      readonly kind: "same-range-different-wrapfn";
      readonly editA: WrapExpressionEdit;
      readonly editB: WrapExpressionEdit;
    };

export type ConflictReport =
  | { readonly kind: "ok"; readonly deduplicatedEdits: readonly EditDescriptor[] }
  | { readonly kind: "conflict"; readonly reason: ConflictReason; readonly edits: readonly EditDescriptor[] };

/**
 * Inspect `edits` for cross-edit conflicts and same-range duplicates.
 *
 * Contract:
 *   - `kind: "ok"` with `deduplicatedEdits` when no conflict was found. Duplicate
 *     `WrapExpressionEdit` (same range + same wrapFn) are collapsed into one
 *     edit (first occurrence wins; stable across insertion order).
 *   - `kind: "conflict"` with a `reason` narrowing to one of three cases:
 *     overlapping ranges, insert-anchor-inside-edit, same-range-different-wrapFn.
 *
 * Algorithm: O(n^2) pairwise scan in input order. Plenty fast for realistic edit
 * counts (dozens per file) and keeps the reason deterministically tied to
 * insertion order (the first conflicting pair wins).
 */
export function detectConflicts(edits: readonly EditDescriptor[]): ConflictReport {
  // Step 1: pairwise scan for conflicts BEFORE dedup — a same-range-different-
  // wrapFn conflict must be reported, not silently deduped.
  for (let i = 0; i < edits.length; i += 1) {
    for (let j = i + 1; j < edits.length; j += 1) {
      const a = edits[i]!;
      const b = edits[j]!;
      const conflict = pairConflict(a, b);
      if (conflict !== null) {
        return { kind: "conflict", reason: conflict, edits };
      }
    }
  }
  // Step 2: no conflicts — dedup same-range same-wrapFn WrapExpressionEdits.
  const deduped = dedupeWrapEdits(edits);
  return { kind: "ok", deduplicatedEdits: deduped };
}

/**
 * Return the conflict (if any) between two edits. Returns null for non-conflict
 * cases including the "two identical wrap edits" duplicate case (handled by
 * dedupeWrapEdits, not here).
 */
function pairConflict(a: EditDescriptor, b: EditDescriptor): ConflictReason | null {
  // Case 1: same-range wraps — distinguish duplicate (same wrapFn) from conflict.
  if (a.kind === "wrap-expression" && b.kind === "wrap-expression") {
    if (rangesEqual(a.range, b.range)) {
      if (a.wrapFn === b.wrapFn) return null; // duplicate — dedupe step collapses.
      return { kind: "same-range-different-wrapfn", editA: a, editB: b };
    }
    if (rangesOverlap(a.range, b.range)) {
      return { kind: "overlapping-ranges", editA: a, editB: b };
    }
    return null;
  }

  // Case 2: two insert-statements — always allowed (both insert at a position;
  // applied in order). Spec §6.4 specifies conflict only when anchor is INSIDE
  // ANOTHER edit's range, not coincident with another insert.
  if (a.kind === "insert-statement" && b.kind === "insert-statement") {
    return null;
  }

  // Case 3: insert-statement anchor overlapping or coincident with another
  // edit's content range — conflict. Spec §6.4 says "anchor.range falling
  // inside another edit's range"; we treat same-or-inside (i.e., any overlap
  // or equality with a non-empty content range) as conflict because the
  // insert would land inside territory being wrapped/replaced.
  if (a.kind === "insert-statement" && b.kind !== "insert-statement") {
    const containerRange = rangeOf(b);
    if (containerRange !== null && anchorConflictsWith(a.anchor.range, containerRange)) {
      return { kind: "insert-anchor-inside-another-edit", insertEdit: a, containingEdit: b };
    }
  }
  if (b.kind === "insert-statement" && a.kind !== "insert-statement") {
    const containerRange = rangeOf(a);
    if (containerRange !== null && anchorConflictsWith(b.anchor.range, containerRange)) {
      return { kind: "insert-anchor-inside-another-edit", insertEdit: b, containingEdit: a };
    }
  }

  // Case 4: overlap between wrap + replace, replace + replace, etc.
  const ra = rangeOf(a);
  const rb = rangeOf(b);
  if (ra !== null && rb !== null && rangesOverlap(ra, rb)) {
    return { kind: "overlapping-ranges", editA: a, editB: b };
  }

  return null;
}

/** Return the edit's primary range, or null for insert-statement (which has an anchor instead). */
function rangeOf(e: EditDescriptor): SourceRange | null {
  if (e.kind === "wrap-expression" || e.kind === "replace-expression") return e.range;
  return null;
}

/** Half-open overlap. Zero-length ranges never "overlap" (both conditions fail). */
function rangesOverlap(a: SourceRange, b: SourceRange): boolean {
  return a.startByte < b.endByte && b.startByte < a.endByte;
}

function rangesEqual(a: SourceRange, b: SourceRange): boolean {
  return a.startByte === b.startByte && a.endByte === b.endByte;
}

/**
 * Does an insert-statement anchor conflict with another edit's non-empty
 * content range?
 *
 * An anchor conflicts if it OVERLAPS the container (standard half-open overlap)
 * OR is fully equal to it (anchor.range == container.range). Boundary-adjacent
 * anchors (e.g., anchor = [endByte, endByte+k)) do NOT conflict — they sit
 * just after the replaced region.
 */
function anchorConflictsWith(anchor: SourceRange, container: SourceRange): boolean {
  if (rangesEqual(anchor, container)) return true;
  return rangesOverlap(anchor, container);
}

/**
 * Remove same-range same-wrapFn duplicates among WrapExpressionEdits. First
 * occurrence wins (stable order). Non-wrap edits pass through unchanged.
 *
 * Safety: this runs only AFTER pairwise conflict scan, so every duplicate we
 * collapse here is genuinely harmless.
 */
function dedupeWrapEdits(edits: readonly EditDescriptor[]): readonly EditDescriptor[] {
  const result: EditDescriptor[] = [];
  const seenWrapKeys = new Set<string>();
  for (const e of edits) {
    if (e.kind === "wrap-expression") {
      const key = `${e.range.startByte}:${e.range.endByte}:${e.wrapFn}`;
      if (seenWrapKeys.has(key)) continue;
      seenWrapKeys.add(key);
    }
    result.push(e);
  }
  return result;
}
