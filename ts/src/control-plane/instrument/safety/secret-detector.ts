/**
 * A2-I safety — secret-literal detector.
 *
 * Spec §5.4: a pattern library scanned against raw bytes BEFORE tree-sitter
 * parsing. Any match flips `SourceFile.hasSecretLiteral = true`. The planner
 * refuses to emit any edit for a file so-flagged (spec §4.4 I3).
 *
 * Pure module. Imports only from `../contract/`. No scanner or FFI dependency.
 *
 * Pattern library (spec §5.4):
 *   - aws-access-key       /AKIA[0-9A-Z]{16}/
 *   - github-pat           /gh[pous]_[A-Za-z0-9]{36,}/
 *   - anthropic-api-key    /sk-ant-[a-zA-Z0-9-_]{95,}/
 *   - openai-api-key       /sk-[a-zA-Z0-9_-]{20,}/  (conservative — may false-positive)
 *   - slack-token          /xox[bpas]-[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{24,}/
 *   - high-entropy-hex     /[0-9a-fA-F]{32,}/ with Shannon-entropy gate (last resort)
 *
 * The Anthropic and OpenAI patterns overlap on `sk-` prefix: we apply the
 * Anthropic pattern first so `sk-ant-...` strings are classified as Anthropic,
 * not OpenAI. The openai-api-key pattern is documented-conservative in the
 * spec; the spec §13 risks table flags variable names like `sk_1234…` as an
 * accepted false-positive. The detector surfaces the match; the error path
 * (planner + pr-body) guides the user to resolve.
 */
import type { SecretMatch } from "../contract/plugin-interface.js";

export type { SecretMatch } from "../contract/plugin-interface.js";

/** One pattern the detector scans for. Order of this list is the order of classification. */
interface PatternSpec {
  readonly id: string;
  readonly regex: RegExp;
  /** Post-filter on the matched substring; used by the entropy heuristic for hex. */
  readonly postFilter?: (match: string) => boolean;
}

// -----------------------------------------------------------------------------
// Pattern list — ordered so more-specific patterns win ties (anthropic before
// openai, etc.). Every regex has `g` flag so `String#matchAll` yields every
// occurrence, not just the first.
// -----------------------------------------------------------------------------

const PATTERNS: readonly PatternSpec[] = [
  {
    id: "aws-access-key",
    regex: /AKIA[0-9A-Z]{16}/g,
  },
  {
    id: "github-pat",
    regex: /gh[pous]_[A-Za-z0-9]{36,}/g,
  },
  {
    id: "anthropic-api-key",
    regex: /sk-ant-[a-zA-Z0-9\-_]{95,}/g,
  },
  {
    id: "openai-api-key",
    // Spec §5.4 pattern: conservative. Excludes `sk-ant-…` (matched above).
    regex: /sk-(?!ant-)[a-zA-Z0-9_\-]{20,}/g,
  },
  {
    id: "slack-token",
    regex: /xox[bpas]-[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{24,}/g,
  },
  {
    id: "high-entropy-hex",
    regex: /[0-9a-fA-F]{32,}/g,
    postFilter: (m) => shannonEntropyBits(m) >= 3.0,
  },
];

/**
 * Scan `bytes` for all secret-literal patterns. Returns one `SecretMatch` per
 * regex hit, in ascending byteOffset order. Empty array when no matches.
 *
 * The bytes are decoded as UTF-8 before scanning. For non-UTF-8 binary files,
 * the decoder's lenient handling may yield replacement characters; that is
 * acceptable because the scanner filters binary files by extension long before
 * reaching this function.
 */
export function detectSecretLiterals(bytes: Buffer): readonly SecretMatch[] {
  const text = bytes.toString("utf-8");
  if (text.length === 0) return [];

  const lineOffsets = buildLineOffsetIndex(text);
  const matches: SecretMatch[] = [];
  const claimedRanges: Array<{ start: number; end: number }> = [];

  for (const spec of PATTERNS) {
    for (const m of text.matchAll(spec.regex)) {
      if (m.index === undefined) continue;
      const matched = m[0];
      if (spec.postFilter && !spec.postFilter(matched)) continue;
      // Avoid duplicate matches for the SAME byte range when a second pattern
      // would re-match it (e.g., high-entropy-hex partially overlapping an
      // already-claimed sk-…).
      if (rangeAlreadyClaimed(claimedRanges, m.index, m.index + matched.length)) continue;
      claimedRanges.push({ start: m.index, end: m.index + matched.length });

      const byteOffset = m.index;
      const lineNumber = lineNumberFromOffset(lineOffsets, byteOffset);
      matches.push({
        pattern: spec.id,
        byteOffset,
        lineNumber,
        excerpt: excerptOf(matched),
      });
    }
  }

  matches.sort((a, b) => a.byteOffset - b.byteOffset);
  return matches;
}

// -----------------------------------------------------------------------------
// Line-offset index — O(log n) byte-to-line lookup via binary search.
// -----------------------------------------------------------------------------

/** Return the 0-based array of line-start byte offsets. Line i starts at result[i]. */
function buildLineOffsetIndex(text: string): readonly number[] {
  const offsets: number[] = [0];
  for (let i = 0; i < text.length; i += 1) {
    if (text.charCodeAt(i) === 10) {
      offsets.push(i + 1);
    }
  }
  return offsets;
}

/** Given a 0-based byte offset, return the 1-based line number. */
function lineNumberFromOffset(lineStarts: readonly number[], byteOffset: number): number {
  let lo = 0;
  let hi = lineStarts.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >>> 1;
    if (lineStarts[mid]! <= byteOffset) {
      lo = mid;
    } else {
      hi = mid - 1;
    }
  }
  return lo + 1;
}

// -----------------------------------------------------------------------------
// Entropy heuristic — keep the generic-hex rule from firing on a.repeat(40).
// -----------------------------------------------------------------------------

/** Shannon entropy (bits per symbol). Target: random hex ≈ 4.0; `aaaa…` → 0.0. */
function shannonEntropyBits(s: string): number {
  if (s.length === 0) return 0;
  const counts = new Map<string, number>();
  for (const ch of s) counts.set(ch, (counts.get(ch) ?? 0) + 1);
  const n = s.length;
  let h = 0;
  for (const c of counts.values()) {
    const p = c / n;
    h -= p * Math.log2(p);
  }
  return h;
}

// -----------------------------------------------------------------------------
// Small helpers
// -----------------------------------------------------------------------------

const EXCERPT_MAX = 40;

function excerptOf(matched: string): string {
  if (matched.length <= EXCERPT_MAX) return matched;
  // Surface the prefix — enough to communicate pattern identity without echoing
  // the full secret to logs.
  return matched.slice(0, EXCERPT_MAX) + "…";
}

function rangeAlreadyClaimed(
  ranges: ReadonlyArray<{ start: number; end: number }>,
  start: number,
  end: number,
): boolean {
  for (const r of ranges) {
    if (start >= r.start && end <= r.end) return true;
    if (start <= r.start && end >= r.end) return true;
    // Partial overlaps don't count as "claimed" — conservative.
  }
  return false;
}
