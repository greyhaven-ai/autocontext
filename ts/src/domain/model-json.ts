/**
 * Generic structured-output extraction from LLM text (AC-937).
 *
 * This parser is a neutral domain leaf: judge and execution may both depend
 * on it, while it depends on neither domain.
 *
 * A port of Python's `harness/core/output_parser.py::extract_json`, kept
 * deliberately close to it in structure so the two can be read side by side.
 * `docs/model-json-extraction-parity-fixtures.json` is generated from the
 * Python implementation and replayed by both languages; that file, not this
 * comment, is the contract.
 *
 * Why this exists rather than another local helper: `ts/src` had five
 * hand-rolled markdown-fence regexes, all subtly different, across ten files
 * doing ad-hoc JSON extraction. The one that was examined closely turned out to
 * carry a live scoring defect (AC-924/AC-937): the judge read a discarded draft
 * out of a reasoning block and scored the run 0.05 where the model said 0.88.
 * Python was in the same state before its consolidation, which found five real
 * defects.
 *
 * The one structural difference from Python is unavoidable. Python defers to
 * `json.JSONDecoder.raw_decode` to find where a value ends, so it never has to
 * reimplement JSON's string-quoting rules. JavaScript has no partial-parse
 * primitive, so `scanValueEnd` below finds candidate boundaries itself. It is
 * kept honest by never being trusted on its own: every span it proposes is
 * handed to `JSON.parse`, which remains the sole authority on whether a
 * candidate is valid.
 */

/**
 * The `tag` group is what makes a ```json fence distinguishable from a bare
 * ``` or a ```python one. JSON is matched case-insensitively but must be the
 * COMPLETE tag: jsonl/json5/jsonnet are different info strings and must not
 * inherit JSON's priority just because they share its prefix. Allowing `{`/`[`
 * in the lookahead preserves the single-line form (```json{"a": 1}```), and
 * U+FEFF lets normal scope cleanup handle a BOM between tag and payload.
 */
const JSON_FENCE_RE = /```(json(?=[\s[{﻿]))?[^\S\n]*\n?([\s\S]*?)\n?[^\S\n]*```/gi;

/**
 * U+FEFF (BOM / zero-width no-break space). `String.prototype.trim()` DOES
 * remove it in JavaScript, unlike Python's `str.strip()`. Stripping it
 * explicitly anyway keeps this function's behavior identical to the Python
 * original rather than relying on a difference between the two runtimes'
 * definitions of whitespace.
 */
const BOM = "﻿";

/**
 * Each failed decode attempt may scan the remaining suffix. Bounding failures
 * keeps adversarial repetition linear in input size while leaving generous
 * recovery for ordinary prose with a handful of stray braces (AC-922).
 */
const MAX_FAILED_DECODE_ATTEMPTS = 64;

const JSON_NUMBER_AT_ARRAY_START_RE =
  /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?(?=\s*(?:[,\]]|$))/;
const NUMERIC_CITATION_RE = /^\[\s*\d+(?:\s*,\s*\d+)*\s*\]$/;

export type OnFailure = "raise" | "none";

export interface ExtractJsonOptions {
  /** `"raise"` (default) rethrows the underlying parse error; `"none"` returns null. */
  onFailure?: OnFailure;
  /** Reject responses carrying more than one top-level JSON object candidate. */
  requireUnique?: boolean;
  /** Skip object candidates that do not contain every named key. */
  requiredKeys?: readonly string[];
}

/** Normalize a candidate scope: strip surrounding whitespace and any leading BOM. */
function scopeText(raw: string): string {
  let out = raw.trim();
  while (out.startsWith(BOM)) out = out.slice(1);
  return out.trim();
}

/**
 * Find the index just past the JSON value starting at `start`, or null.
 *
 * `text[start]` is `{` or `[` -- the only positions this is asked about. Tracks
 * string state and backslash escapes so a brace inside a string value can never
 * be mistaken for a structural one, which is the specific failure a naive
 * brace counter has. Balanced delimiters are necessary but NOT sufficient for
 * validity: the caller always parses the resulting span.
 */
function scanValueEnd(text: string, start: number): number | null {
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let i = start; i < text.length; i += 1) {
    const ch = text[i];
    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (ch === "\\") {
        escaped = true;
      } else if (ch === '"') {
        inString = false;
      }
      continue;
    }
    if (ch === '"') {
      inString = true;
      continue;
    }
    if (ch === "{" || ch === "[") {
      depth += 1;
    } else if (ch === "}" || ch === "]") {
      depth -= 1;
      if (depth === 0) return i + 1;
      if (depth < 0) return null;
    }
  }
  return null;
}

/** Distinguish a truncated JSON array from Markdown/prose brackets. */
function plausibleJsonArrayStart(text: string, start: number): boolean {
  let cursor = start + 1;
  while (cursor < text.length && /\s/.test(text[cursor])) cursor += 1;
  if (cursor === text.length) return true;
  if (['"', "{", "[", "]"].includes(text[cursor])) return true;
  const suffix = text.slice(cursor);
  if (JSON_NUMBER_AT_ARRAY_START_RE.test(suffix)) return true;
  return ["true", "false", "null"].some(
    (literal) =>
      suffix.startsWith(literal) &&
      (suffix.length === literal.length || " \t\r\n,]".includes(suffix[literal.length])),
  );
}

/**
 * Find each top-level JSON value span (`{...}` or `[...]`) in `text`, in order.
 *
 * Searching for `[` as well as `{` matters: a complete array is consumed whole,
 * so a `{` nested inside it is never independently promoted into its own
 * candidate. A malformed object start is skipped and scanning resumes one
 * character later, so side-by-side top-level objects are still returned
 * separately. A malformed *plausible JSON array* is terminal, so its nested
 * objects cannot be promoted, while Markdown/prose brackets such as `[draft]`
 * are skipped like ordinary text.
 */
function topLevelObjectSpans(text: string): string[] {
  const spans: string[] = [];
  let i = 0;
  let failedAttempts = 0;
  for (;;) {
    const braceAt = text.indexOf("{", i);
    const bracketAt = text.indexOf("[", i);
    const starts = [braceAt, bracketAt].filter((p) => p !== -1);
    if (starts.length === 0) return spans;
    const start = Math.min(...starts);

    const end = scanValueEnd(text, start);
    let span: string | null = end === null ? null : text.slice(start, end);
    if (span !== null) {
      try {
        JSON.parse(span);
      } catch {
        span = null;
      }
    }

    if (span === null) {
      failedAttempts += 1;
      if (text[start] === "[" && plausibleJsonArrayStart(text, start)) {
        // A truncated array can still contain complete object values. Treat the
        // whole remainder as one failing candidate instead of walking into it
        // and promoting one of those nested values.
        spans.push(text.slice(start));
        return spans;
      }
      if (failedAttempts >= MAX_FAILED_DECODE_ATTEMPTS) return spans;
      i = start + 1;
      continue;
    }

    spans.push(span);
    i = start + span.length;
  }
}

function fenceMatches(text: string): Array<{ tag: string | undefined; body: string }> {
  const re = new RegExp(JSON_FENCE_RE.source, JSON_FENCE_RE.flags);
  const out: Array<{ tag: string | undefined; body: string }> = [];
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    out.push({ tag: match[1], body: match[2] ?? "" });
    if (match.index === re.lastIndex) re.lastIndex += 1;
  }
  return out;
}

/**
 * Return the fenced scopes to search, or null to search the whole text.
 *
 * Picking the right fence is the whole job. Committing to the FIRST fence of
 * any language was a real regression in Python: a model that emits a reasoning
 * block before its answer put that scratch block in front of the payload, and
 * every recovery path is confined to the scope, so the real payload became
 * unreachable. Worse, when the preamble was a ```python block whose code
 * contained a dict literal, the scope PARSED and the model's scratch work was
 * returned as the answer -- a silent wrong answer rather than a missing one.
 *
 * The tag is the model's own designation, so it decides:
 *
 * 1. Any ```json-tagged fence -> the FIRST one is the sole scope, and it is
 *    terminal. This makes the preamble cases work regardless of what the
 *    preamble contains, and keeps both existing rules: first block wins when
 *    there are two, and a corrupt tagged block fails closed rather than
 *    substituting JSON found elsewhere.
 * 2. Otherwise, untagged fences that could plausibly hold an object -- ones
 *    containing a `{` or `[` -- are tried in order. An untagged fence is a
 *    weaker claim than a tagged one, but it is still a claim, so this stays
 *    confined to the fences and does NOT fall back to surrounding prose.
 * 3. If every fence is brace-free, there is no fenced payload at all. Return
 *    null so the caller scans the whole text, which is what recovers an object
 *    sitting in prose outside those fences.
 */
function fencedPayloadScopes(text: string): string[] | null {
  const matches = fenceMatches(text);
  if (matches.length === 0) return null;
  const tagged = matches.find((m) => m.tag);
  if (tagged) return [scopeText(tagged.body)];
  const scopes = matches.map((m) => scopeText(m.body));
  const plausible = scopes.filter((scope) => scope.includes("{") || scope.includes("["));
  return plausible.length > 0 ? plausible : null;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasRequiredKeys(
  value: Record<string, unknown>,
  requiredKeys: ReadonlySet<string>,
): boolean {
  for (const key of requiredKeys) {
    if (!Object.prototype.hasOwnProperty.call(value, key)) return false;
  }
  return true;
}

/**
 * Whether the response contains competing JSON objects.
 *
 * A tagged JSON fence is authoritative, matching the normal fence-selection
 * rule. Without one, scan the whole response so an object in an untagged
 * reasoning fence cannot hide a different final object outside that fence.
 * Objects missing `requiredKeys` are ineligible, so they are not competitors.
 */
function hasMultipleMappingCandidates(text: string, requiredKeys: ReadonlySet<string>): boolean {
  const tagged = fenceMatches(text).find((m) => m.tag);
  const scope = tagged ? scopeText(tagged.body) : scopeText(text);
  let mappingCount = 0;
  for (const span of topLevelObjectSpans(scope)) {
    let decoded: unknown;
    try {
      decoded = JSON.parse(span);
    } catch {
      continue;
    }
    if (isPlainObject(decoded) && hasRequiredKeys(decoded, requiredKeys)) {
      mappingCount += 1;
      if (mappingCount > 1) return true;
    }
  }
  return false;
}

/** Raised when a candidate parses but is not a JSON object. */
export class WrongJsonTypeError extends Error {}
/** Raised when `requireUnique` is set and the text carries competing objects. */
export class AmbiguousJsonError extends Error {}
/** Raised when an object candidate does not satisfy `requiredKeys`. */
export class MissingRequiredKeysError extends Error {}

/**
 * Extract a JSON object from LLM text.
 *
 * Tries fenced code blocks first, choosing WHICH fence by the rules in
 * `fencedPayloadScopes` rather than taking the first one. If the chosen fence's
 * content does not parse directly, the scan for a `{...}` span stays WITHIN
 * that fenced content -- never the surrounding prose. A broken fence is
 * evidence the model intended that block to be the payload; silently
 * substituting unrelated JSON from elsewhere would return a wrong answer
 * instead of no answer.
 *
 * Only when there is no fenced payload at all does the scan fall back to the
 * whole text. There, multiple candidates are tried in order and the first
 * object wins: loose prose makes no claim to a single payload the way a fence
 * does.
 *
 * A scope whose first plausible JSON container is `[` is exempt from object
 * rescue. If it parses, that is a decisive answer about the model's output
 * shape; if it is truncated, that failure is terminal too rather than a cue to
 * unwrap a nested object.
 *
 * `requiredKeys` filters object candidates without assigning domain semantics
 * to this shared parser. An object missing any required key is skipped so a
 * later candidate can satisfy the caller's schema. `requireUnique` likewise
 * counts only candidates that satisfy the key requirement.
 */
export function extractJson(
  text: string,
  options: ExtractJsonOptions = {},
): Record<string, unknown> | null {
  const { onFailure = "raise", requireUnique = false, requiredKeys = [] } = options;
  const requiredKeySet = new Set(requiredKeys);

  const fencedScopes = fencedPayloadScopes(text);
  const hasFence = fencedScopes !== null;
  const scopes = fencedScopes ?? [scopeText(text)];

  // One flat candidate list across every scope, in scope order and, within a
  // scope, whole-scope before recovered sub-span. Flat rather than a loop per
  // scope because the wrong-type rule below stops the ENTIRE scan, not just the
  // current scope.
  const candidates: string[] = [];
  for (const scope of scopes) {
    candidates.push(scope);

    const arrayStart = scope.indexOf("[");
    const objectStart = scope.indexOf("{");
    if (arrayStart !== -1 && (objectStart === -1 || arrayStart < objectStart)) {
      // The whole scope is already the exact candidate when the array opens it.
      if (arrayStart === 0) continue;
      const structural = topLevelObjectSpans(scope);
      if (structural.length > 0) {
        const firstObjectIndex = structural.findIndex((c) => c.startsWith("{"));
        const citationPrefix =
          arrayStart > 0 &&
          firstObjectIndex > 0 &&
          structural.slice(0, firstObjectIndex).every((c) => NUMERIC_CITATION_RE.test(c));
        if (citationPrefix) {
          candidates.push(structural[firstObjectIndex]);
          continue;
        }
        if (structural[0] !== scope) candidates.push(structural[0]);
      }
      continue;
    }

    if (hasFence) {
      // NOTE: this crude first-`{`-to-last-`}` rescue is weaker than the
      // string-aware span scan the no-fence branch uses, so
      // 'blah {oops} and {"a": 1}' recovers unfenced but not fenced. That
      // divergence is inherited from Python deliberately: unifying it would
      // also change the rule that a fence holding two side-by-side objects is
      // a conflict, which is a separate decision.
      const start = scope.indexOf("{");
      const end = scope.lastIndexOf("}");
      if (start !== -1 && end > start) {
        const braceCandidate = scope.slice(start, end + 1);
        if (braceCandidate !== scope) candidates.push(braceCandidate);
      }
    } else {
      for (const span of topLevelObjectSpans(scope)) {
        if (span !== scope) candidates.push(span);
      }
    }
  }

  let lastError: Error | null = null;
  for (const candidate of candidates) {
    let decoded: unknown;
    try {
      decoded = JSON.parse(candidate);
    } catch (err) {
      lastError = err instanceof Error ? err : new SyntaxError(String(err));
      continue;
    }
    if (isPlainObject(decoded)) {
      if (!hasRequiredKeys(decoded, requiredKeySet)) {
        const missing = [...requiredKeySet].filter(
          (key) => !Object.prototype.hasOwnProperty.call(decoded, key),
        );
        lastError = new MissingRequiredKeysError(
          `Expected JSON object containing required keys: ${JSON.stringify(missing.sort())}`,
        );
        continue;
      }
      if (requireUnique && hasMultipleMappingCandidates(text, requiredKeySet)) {
        lastError = new AmbiguousJsonError(
          "Expected one unambiguous JSON object, got multiple candidates",
        );
        break;
      }
      return decoded;
    }
    // A candidate that PARSES but is not an object is a decisive answer about
    // what the model produced, not a parse failure to recover from. Stopping
    // here is what prevents grabbing an object-shaped fragment nested inside an
    // array and silently returning it.
    lastError = new WrongJsonTypeError(
      `Expected JSON object, got ${Array.isArray(decoded) ? "list" : typeof decoded}`,
    );
    break;
  }

  if (lastError === null) {
    lastError = new SyntaxError("No JSON object found in text");
  }
  if (onFailure === "none") return null;
  throw lastError;
}

/**
 * Strip the first markdown code fence, returning its inner content.
 *
 * Compatibility wrapper that intentionally keeps the historical first-fence
 * behavior. JSON-parsing callers should use `extractJson`, which can
 * distinguish a designated JSON answer from an earlier reasoning fence.
 */
export function stripJsonFences(text: string): string {
  const matches = fenceMatches(text);
  return matches.length > 0 ? matches[0].body.trim() : text.trim();
}
