/**
 * A2-I scanner — SourceFile wrapper.
 *
 * Builds a SourceFile instance (spec §4.3) from a raw file on disk:
 *
 *   - reads bytes
 *   - parses directives (see spec §5.3) — `# autocontext: off` on the preceding
 *     line marks the next line; `off-file` disables until EOF; `on-file` re-enables;
 *     directives inside string literals are NOT honored (tokenizer-aware parse).
 *   - parses existingImports via a lightweight regex scan sufficient for the
 *     import-manager's dedup needs (tree-sitter is not required for this)
 *   - detects indentation style by scanning leading whitespace
 *   - `hasSecretLiteral` stubbed `false` in Layer 2 — Layer 3 secret-detector
 *     fills this in before the planner sees the SourceFile.
 *   - `tree` is lazy — parsed only on first access by plugins that need the CST.
 */
import { readFile } from "node:fs/promises";
import type {
  DirectiveMap,
  DirectiveValue,
  ExistingImport,
  ImportSet,
  IndentationStyle,
  InstrumentLanguage,
  SourceFile,
} from "../contract/plugin-interface.js";
import { parseSource } from "./tree-sitter-loader.js";

// Python directive regex — must sit at line start (after optional leading whitespace).
const PY_DIRECTIVE = /^\s*#\s*autocontext:\s*(off|on|off-file|on-file)\s*(?:#.*)?$/;
// JS/TS directive regex — `// autocontext: off` or `/* autocontext: off */`.
const JS_DIRECTIVE = /^\s*(?:\/\/|\/\*)\s*autocontext:\s*(off|on|off-file|on-file)\s*(?:\*\/)?\s*$/;

/** Load a SourceFile from disk. Tree parsing is deferred until `.tree` is first read. */
export async function loadSourceFile(args: {
  readonly path: string;
  readonly language: InstrumentLanguage;
}): Promise<SourceFile> {
  const bytes = await readFile(args.path);
  return fromBytes({ path: args.path, language: args.language, bytes });
}

/** Construct a SourceFile from raw bytes. Useful for tests and in-memory composition. */
export function fromBytes(args: {
  readonly path: string;
  readonly language: InstrumentLanguage;
  readonly bytes: Buffer;
}): SourceFile {
  const { path, language, bytes } = args;
  const content = bytes.toString("utf-8");
  const lines = content.split(/\r?\n/);

  const directives = parseDirectives(lines, language);
  const existingImports = parseExistingImports(lines, language);
  const indentationStyle = detectIndentationStyle(lines);

  // Lazy tree — compute on first access and memoize on the object itself.
  let cachedTree: unknown | undefined = undefined;
  const file: SourceFile = {
    path,
    language,
    bytes,
    get tree(): unknown {
      if (cachedTree === undefined) {
        cachedTree = parseSource(language, bytes);
      }
      return cachedTree;
    },
    directives,
    hasSecretLiteral: false, // Layer 2: stub; Layer 3 secret-detector fills in.
    existingImports,
    indentationStyle,
  };
  return file;
}

// ---------------------------------------------------------------------------
// Directive parser — tokenizer-aware enough to skip string-literal contents.
// ---------------------------------------------------------------------------

/**
 * Parse autocontext directives from `lines`. Directive semantics:
 *   - `off` / `on`  at line N → recorded against line N+1 (next-line scope)
 *   - `off-file` / `on-file` at line N → recorded against line N
 *   - Directives that appear inside a string literal (single-line or triple-quoted)
 *     are NOT honored.
 *
 * We track multi-line string state only (triple-quotes in Python, block comments
 * in JS/TS). Single-line string literals can never legitimately contain a
 * directive matching the regex because the regex is anchored at start-of-line
 * after optional whitespace — i.e. it requires the `#` / `//` / `/*` at column 0
 * (modulo indentation), which cannot occur inside a same-line string literal.
 */
export function parseDirectives(
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

// ---------------------------------------------------------------------------
// Existing imports — lightweight regex scan (sufficient for dedup needs)
// ---------------------------------------------------------------------------

const PY_FROM_IMPORT = /^\s*from\s+([\w.]+)\s+import\s+(.+)$/;
const PY_IMPORT = /^\s*import\s+([\w.]+(?:\s*,\s*[\w.]+)*)\s*$/;
const JS_IMPORT_NAMED = /^\s*import\s+\{([^}]*)\}\s+from\s+['"]([^'"]+)['"]\s*;?\s*$/;
const JS_IMPORT_DEFAULT = /^\s*import\s+(\w+)\s+from\s+['"]([^'"]+)['"]\s*;?\s*$/;
const JS_IMPORT_NAMESPACE = /^\s*import\s+\*\s+as\s+(\w+)\s+from\s+['"]([^'"]+)['"]\s*;?\s*$/;
const JS_IMPORT_SIDEEFFECT = /^\s*import\s+['"]([^'"]+)['"]\s*;?\s*$/;

export function parseExistingImports(
  lines: readonly string[],
  language: InstrumentLanguage,
): ImportSet {
  const byModule = new Map<string, Set<string>>();
  const add = (module: string, name: string): void => {
    const s = byModule.get(module) ?? new Set<string>();
    s.add(name);
    byModule.set(module, s);
  };

  if (language === "python") {
    for (const line of lines) {
      const fromImp = line.match(PY_FROM_IMPORT);
      if (fromImp) {
        const module = fromImp[1]!;
        const body = fromImp[2]!;
        for (const part of body.split(",")) {
          const token = part.trim().split(/\s+as\s+/)[0]!.trim();
          if (!token) continue;
          const cleaned = token.replace(/^\(|\)$/g, "").trim();
          if (cleaned) add(module, cleaned);
        }
        continue;
      }
      const imp = line.match(PY_IMPORT);
      if (imp) {
        const body = imp[1]!;
        for (const part of body.split(",")) {
          const mod = part.trim().split(/\s+as\s+/)[0]!.trim();
          if (mod) add(mod, mod);
        }
      }
    }
  } else {
    for (const line of lines) {
      const named = line.match(JS_IMPORT_NAMED);
      if (named) {
        const body = named[1]!;
        const module = named[2]!;
        for (const part of body.split(",")) {
          const name = part.trim().split(/\s+as\s+/)[0]!.trim();
          if (name) add(module, name);
        }
        continue;
      }
      const def = line.match(JS_IMPORT_DEFAULT);
      if (def) {
        add(def[2]!, def[1]!);
        continue;
      }
      const ns = line.match(JS_IMPORT_NAMESPACE);
      if (ns) {
        add(ns[2]!, ns[1]!);
        continue;
      }
      const side = line.match(JS_IMPORT_SIDEEFFECT);
      if (side) {
        if (!byModule.has(side[1]!)) byModule.set(side[1]!, new Set());
      }
    }
  }

  const result = new Set<ExistingImport>();
  const keys = Array.from(byModule.keys()).sort();
  for (const module of keys) {
    result.add({ module, names: byModule.get(module)! });
  }
  return result;
}

// ---------------------------------------------------------------------------
// Indentation detection — picks the GCD of observed leading-width counts.
// ---------------------------------------------------------------------------

/** Detect indentation style from lines' leading whitespace. Defaults to 4-space. */
export function detectIndentationStyle(lines: readonly string[]): IndentationStyle {
  let tabLines = 0;
  const widths: number[] = [];

  for (const line of lines) {
    if (line.length === 0) continue;
    let i = 0;
    while (i < line.length && (line[i] === " " || line[i] === "\t")) i += 1;
    if (i === 0) continue;
    const leading = line.slice(0, i);
    if (leading.includes("\t")) {
      tabLines += 1;
      continue;
    }
    widths.push(leading.length);
  }

  if (tabLines > 0 && tabLines >= widths.length) return { kind: "tabs" };
  if (widths.length === 0) return { kind: "spaces", width: 4 };

  // Take the GCD of all observed widths. Clamp to [2, 8] — pathological inputs
  // (e.g., single-space accidental indent) default to 4.
  const g = widths.reduce((acc, w) => gcd(acc, w), widths[0]!);
  if (g <= 1) return { kind: "spaces", width: 4 };
  if (g >= 8) return { kind: "spaces", width: 8 };
  return { kind: "spaces", width: g };
}

function gcd(a: number, b: number): number {
  let x = Math.abs(a);
  let y = Math.abs(b);
  while (y !== 0) {
    const t = y;
    y = x % y;
    x = t;
  }
  return x;
}
