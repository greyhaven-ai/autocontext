/**
 * A2-I Layer 5 — indentation matcher (spec §6.3).
 *
 * Re-indents a raw multi-line statement to match the enclosing scope's
 * indentation. Used by InsertStatementEdit composition.
 *
 * Algorithm:
 *   1. Determine enclosing indentation — prefer the PREVIOUS non-blank line's
 *      leading whitespace (nearest-neighbor), falling back to the file's
 *      detected `indentationStyle` times (depth=0 → empty).
 *   2. Strip the COMMON leading whitespace prefix from rawStatement.
 *   3. Re-apply the enclosing indentation to every non-empty line of the
 *      stripped statement.
 *
 * Layer 1+2 concern addressed:
 *   GCD-based detection could under-detect width on sparsely-indented files.
 *   Nearest-neighbor look-up (step 1a) tolerates this — we use the ACTUAL
 *   preceding-line indent rather than inferring from file-level style.
 *
 * Never auto-formats the entire file — only adjusts the lines the framework
 * inserts.
 *
 * Import discipline (spec §3.3):
 *   - imports from instrument/contract/ only
 *   - NO imports from sibling planner modules
 */
import type { SourceFile } from "../contract/plugin-interface.js";

export interface MatchIndentationOpts {
  readonly sourceFile: SourceFile;
  /** 1-based line number the new statement inserts before/after. */
  readonly anchorLine: number;
  /** Multi-line statement source, with whatever indent the plugin emitted. */
  readonly rawStatement: string;
}

/**
 * Produce a re-indented copy of `rawStatement` that matches the enclosing
 * scope's indentation at `anchorLine`.
 */
export function matchIndentation(opts: MatchIndentationOpts): string {
  const { sourceFile, anchorLine, rawStatement } = opts;
  const enclosing = resolveEnclosingIndent(sourceFile, anchorLine);
  const lines = rawStatement.split("\n");
  const common = commonLeadingWhitespace(lines);
  const stripped = lines.map((l) => (l.startsWith(common) ? l.slice(common.length) : l));
  const reindented = stripped.map((l) => (l.length === 0 ? l : enclosing + l));
  return reindented.join("\n");
}

/**
 * Find the indentation to apply to a new statement inserted at `anchorLine`.
 *
 * Strategy (nearest-neighbor first, then file-style fallback):
 *   1. Walk backward from `anchorLine - 1` looking for the first non-blank
 *      line; use ITS leading whitespace.
 *   2. If none found, fall back to the empty string (top-level).
 *
 * This tolerates the sparsely-indented edge case where GCD-based detection
 * under-reports the file's indent width: the nearest non-blank line carries
 * authoritative information about the local scope's indent.
 */
function resolveEnclosingIndent(sourceFile: SourceFile, anchorLine: number): string {
  const text = sourceFile.bytes.toString("utf-8");
  const lines = text.split(/\r?\n/);
  // anchorLine is 1-based; walk backward from anchorLine - 1 (0-based).
  for (let i = Math.min(anchorLine - 2, lines.length - 1); i >= 0; i -= 1) {
    const ln = lines[i]!;
    if (ln.trim().length === 0) continue;
    return leadingWhitespace(ln);
  }
  return "";
}

/** Return the leading whitespace (spaces or tabs) of a line. */
function leadingWhitespace(line: string): string {
  let i = 0;
  while (i < line.length && (line[i] === " " || line[i] === "\t")) i += 1;
  return line.slice(0, i);
}

/**
 * Longest common leading-whitespace prefix across every non-blank line.
 * Blank lines are ignored (they carry no indentation info). Returns "" when
 * there's no common indentation.
 */
function commonLeadingWhitespace(lines: readonly string[]): string {
  let common: string | null = null;
  for (const l of lines) {
    if (l.trim().length === 0) continue;
    const lead = leadingWhitespace(l);
    if (common === null) {
      common = lead;
      continue;
    }
    // Longest common prefix of `common` and `lead`.
    let k = 0;
    const n = Math.min(common.length, lead.length);
    while (k < n && common[k] === lead[k]) k += 1;
    common = common.slice(0, k);
    if (common.length === 0) break;
  }
  return common ?? "";
}
