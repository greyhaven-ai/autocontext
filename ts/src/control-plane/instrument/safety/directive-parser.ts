/**
 * A2-I safety — inline directive parser.
 *
 * Spec §5.3 defines the comment-directive language:
 *   - Python:       `# autocontext: (off|on|off-file|on-file)`
 *   - JS/TS/JSX:    `// autocontext: (…)` or `/* autocontext: (…) *\/`
 *
 * Semantics:
 *   - `off` at line N → recorded at line N+1 (next-line scope)
 *   - `on`  at line N → recorded at line N+1
 *   - `off-file` / `on-file` at line N → recorded at line N (effect persists to EOF or next toggle)
 *   - Directives inside a multi-line string literal or block comment are NOT
 *     honored (tokenizer respects string/comment distinction)
 *
 * Canonical home: `safety/directive-parser.ts`. Layers 1+2 shipped an inline
 * equivalent in `scanner/source-file.ts`; Layer 3 extracts to here and the
 * scanner imports this function. Behavior is identical to the prior inline
 * version (same regex, same triple-quote + block-comment tracking) — Layer 1+2
 * tests continue to pass via `scanner/source-file.ts`'s re-export.
 *
 * Import discipline (spec §3.3):
 *   - imports `contract/` (for types)
 *   - no imports from scanner/ (avoids cycle; scanner/source-file.ts imports
 *     the parser from HERE, not the other way)
 */
import type {
  DirectiveMap,
  DirectiveValue,
  InstrumentLanguage,
} from "../contract/plugin-interface.js";

// Python directive — must sit at line start (after optional leading whitespace).
const PY_DIRECTIVE = /^\s*#\s*autocontext:\s*(off|on|off-file|on-file)\s*(?:#.*)?$/;
// JS/TS directive — `// autocontext: off` or `/* autocontext: off */`.
const JS_DIRECTIVE = /^\s*(?:\/\/|\/\*)\s*autocontext:\s*(off|on|off-file|on-file)\s*(?:\*\/)?\s*$/;

/**
 * Parse autocontext directives from UTF-8 `bytes` for `language`.
 *
 * Splits on `\r?\n`. Returns a `DirectiveMap` keyed by 1-based line number.
 * Does not throw; malformed directives simply fail to match the regex and are
 * ignored (matching existing `# noqa` conventions).
 */
export function parseDirectives(bytes: Buffer, language: InstrumentLanguage): DirectiveMap {
  const text = bytes.toString("utf-8");
  const lines = text.split(/\r?\n/);
  return parseDirectivesFromLines(lines, language);
}

/**
 * Line-oriented variant. Exposed so `scanner/source-file.ts` can reuse when it
 * has already split the source content into lines for other passes (indentation
 * detection, import parsing) — avoids re-splitting the file.
 */
export function parseDirectivesFromLines(
  lines: readonly string[],
  language: InstrumentLanguage,
): DirectiveMap {
  const map = new Map<number, DirectiveValue>();
  const pattern = language === "python" ? PY_DIRECTIVE : JS_DIRECTIVE;

  // Python triple-quote state.
  let inPyTripleSingle = false;
  let inPyTripleDouble = false;
  // JS/TS block-comment state for multi-line /* ... */.
  let inJsBlockComment = false;

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i]!;
    const lineNumber1 = i + 1;

    // Snapshot "was inside string/comment at start-of-line" — directives on such
    // lines are NOT honored even if they match the regex, because the line opens
    // inside a multi-line string or block comment.
    const wasInsideAtStart =
      language === "python"
        ? inPyTripleSingle || inPyTripleDouble
        : inJsBlockComment;

    if (language === "python") {
      const next = scanPythonTripleStrings(line, inPyTripleSingle, inPyTripleDouble);
      inPyTripleSingle = next.inTripleSingle;
      inPyTripleDouble = next.inTripleDouble;
    } else {
      inJsBlockComment = scanJsBlockComment(line, inJsBlockComment);
    }

    if (wasInsideAtStart) continue;

    const match = line.match(pattern);
    if (!match) continue;

    const raw = match[1] as DirectiveValue;
    if (raw === "off-file" || raw === "on-file") {
      map.set(lineNumber1, raw);
    } else {
      map.set(lineNumber1 + 1, raw);
    }
  }
  return map;
}

/**
 * Scan `line` for Python triple-quote openings/closings. Returns the updated
 * in-triple state at end-of-line. Regular single/double-quoted strings on the
 * same line do NOT affect state (they must close on the same line per Python
 * lexer rules).
 */
function scanPythonTripleStrings(
  line: string,
  inSingleInitial: boolean,
  inDoubleInitial: boolean,
): { readonly inTripleSingle: boolean; readonly inTripleDouble: boolean } {
  let inSingle = inSingleInitial;
  let inDouble = inDoubleInitial;

  let i = 0;
  while (i < line.length) {
    const rest = line.slice(i);
    if (inSingle) {
      if (rest.startsWith("'''")) {
        inSingle = false;
        i += 3;
        continue;
      }
      i += 1;
      continue;
    }
    if (inDouble) {
      if (rest.startsWith('"""')) {
        inDouble = false;
        i += 3;
        continue;
      }
      i += 1;
      continue;
    }
    if (rest.startsWith("'''")) {
      inSingle = true;
      i += 3;
      continue;
    }
    if (rest.startsWith('"""')) {
      inDouble = true;
      i += 3;
      continue;
    }
    // Outside a triple — skip single-line strings + everything else.
    const ch = line[i]!;
    if (ch === '"' || ch === "'") {
      // Skip to matching closing single-line quote; abort at EOL if unclosed.
      i = skipSingleLineString(line, i, ch);
      continue;
    }
    i += 1;
  }
  return { inTripleSingle: inSingle, inTripleDouble: inDouble };
}

function skipSingleLineString(line: string, start: number, quote: string): number {
  // start points at the opening quote; advance past escapes to matching quote.
  let i = start + 1;
  while (i < line.length) {
    if (line[i] === "\\") {
      i += 2;
      continue;
    }
    if (line[i] === quote) {
      return i + 1;
    }
    i += 1;
  }
  return line.length;
}

/** Returns true if end-of-line is inside a block comment. */
function scanJsBlockComment(line: string, inBlockInitial: boolean): boolean {
  let i = 0;
  let inBlock = inBlockInitial;
  while (i < line.length) {
    const rest = line.slice(i);
    if (inBlock) {
      const closeIdx = rest.indexOf("*/");
      if (closeIdx === -1) return true; // rest of line inside block
      inBlock = false;
      i += closeIdx + 2;
      continue;
    }
    // Outside block: skip strings and `//` line comments + look for `/*`.
    const ch = line[i]!;
    if (ch === '"' || ch === "'" || ch === "`") {
      i = skipSingleLineString(line, i, ch);
      continue;
    }
    if (rest.startsWith("//")) return inBlock; // rest is line comment — can't open block
    if (rest.startsWith("/*")) {
      inBlock = true;
      i += 2;
      continue;
    }
    i += 1;
  }
  return inBlock;
}
