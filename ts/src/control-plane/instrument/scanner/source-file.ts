/**
 * A2-I scanner — SourceFile wrapper.
 *
 * Builds a SourceFile instance (spec §4.3) from a raw file on disk:
 *
 *   - reads bytes
 *   - parses directives via `safety/directive-parser.ts` (Layer 3 canonical home)
 *   - parses existingImports via a lightweight regex scan sufficient for the
 *     import-manager's dedup needs (tree-sitter is not required for this)
 *   - detects indentation style by scanning leading whitespace
 *   - detects secret literals via `safety/secret-detector.ts` (Layer 3);
 *     populates `hasSecretLiteral` + `secretMatches`
 *   - `tree` is lazy — parsed only on first access by plugins that need the CST.
 *
 * Import direction note (Layer 3):
 *   scanner/source-file.ts imports from safety/ (directive-parser, secret-detector).
 *   safety/* primitives themselves import ONLY from contract/ — so there is no
 *   cycle. Spec §3.3's "safety imports scanner" permission remains available
 *   for future safety features that legitimately need scanner primitives (e.g.,
 *   post-tree-sitter secret detection that narrows scans to string-literal
 *   tokens). None of Layer 3's primitives need it.
 */
import { readFile } from "node:fs/promises";
import type {
  ExistingImport,
  ImportedName,
  ImportSet,
  IndentationStyle,
  InstrumentLanguage,
  SourceFile,
} from "../contract/plugin-interface.js";
import { parseSource, parseSync } from "./tree-sitter-loader.js";
import {
  parseDirectives as safetyParseDirectives,
  parseDirectivesFromLines,
} from "../safety/directive-parser.js";
import { detectSecretLiterals } from "../safety/secret-detector.js";

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

  // Safety primitives fill the two A2-I safety floors (directives + secrets).
  const directives = parseDirectivesFromLines(lines, language);
  const secretMatches = detectSecretLiterals(bytes);
  const hasSecretLiteral = secretMatches.length > 0;

  const existingImports = parseExistingImports(lines, language);
  const indentationStyle = detectIndentationStyle(lines);

  // Lazy tree — compute on first access and memoize on the object itself.
  // After A2-II-b Fix 1: uses `parseSync` (synchronous, requires the parser
  // to have been preloaded via `ensureParserLoaded` in the orchestrator's
  // pre-loop phase). Falls back to the async `parseSource` path only when
  // called outside the orchestrator (e.g. in scanner unit tests that call
  // `fromBytes` directly without preloading — those tests use `sourceFile.tree`
  // lazily and the Promise is acceptable since they don't drive queries).
  let cachedTree: unknown | undefined = undefined;
  const file: SourceFile = {
    path,
    language,
    bytes,
    get tree(): unknown {
      if (cachedTree === undefined) {
        // Use synchronous parse. If the parser has been preloaded by the
        // orchestrator this is instant. If not (standalone unit test usage),
        // this throws — callers outside the orchestrator should use
        // `parseSource` directly.
        try {
          cachedTree = parseSync(language, bytes);
        } catch {
          // Parser not yet loaded (unit test context without orchestrator
          // preload). Store the Promise so repeated accesses are idempotent.
          cachedTree = parseSource(language, bytes);
        }
      }
      return cachedTree;
    },
    directives,
    hasSecretLiteral,
    secretMatches,
    existingImports,
    indentationStyle,
  };
  return file;
}

// ---------------------------------------------------------------------------
// Directive parser — delegated to safety/. Re-exported here for backward
// compatibility with Layer 1+2 test suite + scanner barrel.
// ---------------------------------------------------------------------------

/**
 * Back-compat shim. Layer 1+2 shipped `parseDirectives(lines, language)` here;
 * Layer 3 moves the canonical impl into `safety/directive-parser.ts`. Tests
 * and any downstream importers that still use the `lines` form continue to
 * work via this thin adapter.
 */
export function parseDirectives(
  lines: readonly string[],
  language: InstrumentLanguage,
): ReturnType<typeof parseDirectivesFromLines> {
  return parseDirectivesFromLines(lines, language);
}

// Re-export the safety form so callers with a Buffer in hand don't have to
// split lines themselves.
export { safetyParseDirectives as parseDirectivesFromBytes };

// ---------------------------------------------------------------------------
// Existing imports — lightweight regex scan (sufficient for dedup needs)
// ---------------------------------------------------------------------------

const PY_FROM_IMPORT = /^\s*from\s+([\w.]+)\s+import\s+(.+)$/;
const PY_IMPORT = /^\s*import\s+([\w.]+(?:\s+as\s+\w+)?(?:\s*,\s*[\w.]+(?:\s+as\s+\w+)?)*)\s*$/;
const JS_IMPORT_NAMED = /^\s*import\s+\{([^}]*)\}\s+from\s+['"]([^'"]+)['"]\s*;?\s*$/;
const JS_IMPORT_DEFAULT = /^\s*import\s+(\w+)\s+from\s+['"]([^'"]+)['"]\s*;?\s*$/;
const JS_IMPORT_NAMESPACE = /^\s*import\s+\*\s+as\s+(\w+)\s+from\s+['"]([^'"]+)['"]\s*;?\s*$/;
const JS_IMPORT_SIDEEFFECT = /^\s*import\s+['"]([^'"]+)['"]\s*;?\s*$/;

export function parseExistingImports(
  lines: readonly string[],
  language: InstrumentLanguage,
): ImportSet {
  const byModule = new Map<string, Set<ImportedName>>();
  const add = (module: string, entry: ImportedName): void => {
    const s = byModule.get(module) ?? new Set<ImportedName>();
    s.add(entry);
    byModule.set(module, s);
  };

  if (language === "python") {
    for (const line of lines) {
      const fromImp = line.match(PY_FROM_IMPORT);
      if (fromImp) {
        const module = fromImp[1]!;
        const body = fromImp[2]!;
        for (const part of body.split(",")) {
          const cleaned = part.trim().replace(/^\(|\)$/g, "").trim();
          if (!cleaned) continue;
          const segments = cleaned.split(/\s+as\s+/).map((s) => s.trim());
          const name = segments[0]!;
          const alias = segments[1] || undefined;
          if (name) add(module, { name, alias });
        }
        continue;
      }
      const imp = line.match(PY_IMPORT);
      if (imp) {
        const body = imp[1]!;
        for (const part of body.split(",")) {
          const segments = part.trim().split(/\s+as\s+/).map((s) => s.trim());
          const mod = segments[0]!;
          const alias = segments[1] || mod;
          if (mod) add(mod, { name: mod, alias });
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
          const trimmed = part.trim();
          if (!trimmed) continue;
          const segments = trimmed.split(/\s+as\s+/).map((s) => s.trim());
          const name = segments[0]!;
          const alias = segments[1] || undefined;
          if (name) add(module, { name, alias });
        }
        continue;
      }
      const def = line.match(JS_IMPORT_DEFAULT);
      if (def) {
        add(def[2]!, { name: "default", alias: def[1]! });
        continue;
      }
      const ns = line.match(JS_IMPORT_NAMESPACE);
      if (ns) {
        // namespace import: `import * as alias from "mod"` — store name = mod, alias = alias
        add(ns[2]!, { name: ns[2]!, alias: ns[1]! });
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
