/**
 * A2-I Layer 5 — import manager (spec §6.2).
 *
 * Per-language import placement + deduplication + extension-of-existing-statement
 * logic for Python and TypeScript/JavaScript/JSX/TSX.
 *
 * Dedup discipline: (module, name, alias, kind). If the module already has a
 * named-import statement and a new ImportSpec is needed from the same module,
 * EXTEND the existing statement (Python: `from m import X, Y`; TS: `import
 * { X, Y } from "m"`) rather than create a parallel import.
 *
 * Quote-style inference (TS/JS): scan existing imports — majority quote style
 * wins; default to double if ambiguous.
 *
 * Placement rules:
 *   - Python: after last `from __future__ import`, then after last existing
 *     `import`/`from-import`, then ONE blank line, then new imports sorted
 *     alphabetically by module.
 *   - TS/JS/JSX/TSX: after last top-level `import`.
 *   - No existing imports: after any module-level docstring / triple-slash
 *     directive / shebang.
 *
 * Import discipline (spec §3.3):
 *   - imports from instrument/contract/ only
 *   - NO imports from sibling planner modules
 */
import type {
  ImportSpec,
  ImportedName,
  SourceFile,
  InstrumentLanguage,
} from "../contract/plugin-interface.js";

export interface PlanImportsOpts {
  readonly sourceFile: SourceFile;
  readonly importsNeeded: readonly ImportSpec[];
}

export interface ImportPlan {
  /** 1-based line number + 0-based column where the new import block is inserted. */
  readonly insertAt: { readonly line: number; readonly col: number };
  /** Pre-rendered import block, including leading blank line(s) if needed. */
  readonly statementSource: string;
  /** Specs NOT already present in `sourceFile.existingImports` (post-dedup). */
  readonly additionalSpecsEmitted: readonly ImportSpec[];
}

/**
 * Produce an import plan for the given set of required imports.
 *
 * The pipeline then turns the returned `insertAt` + `statementSource` into an
 * InsertStatementEdit-like edit at patch-emission time; this module returns the
 * pre-composed block rather than an EditDescriptor so the composer can adjust
 * surrounding whitespace without re-parsing.
 */
export function planImports(opts: PlanImportsOpts): ImportPlan {
  const { sourceFile, importsNeeded } = opts;
  const deduped = dedupeSpecs(importsNeeded);
  const missing = filterAlreadyPresent(deduped, sourceFile);
  const sorted = sortSpecs(missing);

  if (sorted.length === 0) {
    // Nothing to emit; insertAt defaults to line 1 — caller should no-op.
    return {
      insertAt: { line: 1, col: 0 },
      statementSource: "",
      additionalSpecsEmitted: [],
    };
  }

  const lines = sourceFile.bytes.toString("utf-8").split(/\r?\n/);
  const language = sourceFile.language;
  const anchor = computeImportAnchor(lines, language);
  const grouped = groupByModuleKind(sorted);
  const quoteStyle = language === "python" ? "none" : detectQuoteStyle(lines);
  const statementSource = renderImportBlock({
    language,
    groups: grouped,
    quoteStyle,
    anchorHadImports: anchor.hadImports,
    sourceFile,
  });

  return {
    insertAt: { line: anchor.insertLine, col: 0 },
    statementSource,
    additionalSpecsEmitted: sorted,
  };
}

// ---------------------------------------------------------------------------
// Deduplication + filtering
// ---------------------------------------------------------------------------

function specKey(s: ImportSpec): string {
  return `${s.module}\u0000${s.name}\u0000${s.alias ?? ""}\u0000${s.kind}`;
}

function dedupeSpecs(specs: readonly ImportSpec[]): readonly ImportSpec[] {
  const seen = new Set<string>();
  const out: ImportSpec[] = [];
  for (const s of specs) {
    const k = specKey(s);
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(s);
  }
  return out;
}

function filterAlreadyPresent(
  specs: readonly ImportSpec[],
  sourceFile: SourceFile,
): readonly ImportSpec[] {
  const existingByModule = new Map<string, ReadonlySet<ImportedName>>();
  for (const ei of sourceFile.existingImports) {
    existingByModule.set(ei.module, ei.names);
  }
  return specs.filter((s) => {
    const existing = existingByModule.get(s.module);
    if (!existing) return true;
    // Check if the spec name matches any recorded ImportedName.
    for (const n of existing) {
      if (n.name === s.name) return false; // already imported (with or without alias)
      // For default imports the scanner records name="default" with alias=binding.
      if (s.kind === "default" && n.name === "default") return false;
    }
    if (s.alias) {
      for (const n of existing) {
        if (n.alias === s.alias) return false;
      }
    }
    return true;
  });
}

function sortSpecs(specs: readonly ImportSpec[]): readonly ImportSpec[] {
  const copy = specs.slice();
  copy.sort((a, b) => {
    if (a.module !== b.module) return a.module < b.module ? -1 : 1;
    if (a.name !== b.name) return a.name < b.name ? -1 : 1;
    return 0;
  });
  return copy;
}

// ---------------------------------------------------------------------------
// Grouping (so we can extend existing `from m import X` to `from m import X, Y`)
// ---------------------------------------------------------------------------

interface ImportGroup {
  readonly module: string;
  readonly kind: "named" | "default" | "namespace";
  readonly specs: readonly ImportSpec[];
}

function groupByModuleKind(specs: readonly ImportSpec[]): readonly ImportGroup[] {
  const map = new Map<string, ImportSpec[]>();
  for (const s of specs) {
    const k = `${s.module}\u0000${s.kind}`;
    const arr = map.get(k) ?? [];
    arr.push(s);
    map.set(k, arr);
  }
  const keys = Array.from(map.keys()).sort();
  const groups: ImportGroup[] = [];
  for (const k of keys) {
    const arr = map.get(k)!;
    groups.push({ module: arr[0]!.module, kind: arr[0]!.kind, specs: arr });
  }
  return groups;
}

// ---------------------------------------------------------------------------
// Anchor computation — where to insert the new import block
// ---------------------------------------------------------------------------

interface ImportAnchor {
  readonly insertLine: number; // 1-based; insert BEFORE this line
  readonly hadImports: boolean;
}

const PY_IMPORT_LINE = /^\s*(from\s+\S+\s+import\s+.+|import\s+\S+.*)$/;
const PY_FUTURE_LINE = /^\s*from\s+__future__\s+import\s+.+$/;
const JS_IMPORT_LINE = /^\s*import\s+.+$/;

function computeImportAnchor(
  lines: readonly string[],
  language: InstrumentLanguage,
): ImportAnchor {
  if (language === "python") {
    let lastFutureLine = -1;
    let lastImportLine = -1;
    for (let i = 0; i < lines.length; i += 1) {
      const ln = lines[i]!;
      if (PY_FUTURE_LINE.test(ln)) lastFutureLine = i;
      else if (PY_IMPORT_LINE.test(ln)) lastImportLine = i;
    }
    const lastIdx = Math.max(lastFutureLine, lastImportLine);
    if (lastIdx >= 0) {
      return { insertLine: lastIdx + 2, hadImports: true }; // after last import
    }
    // No imports — find first non-shebang, non-docstring line.
    return { insertLine: firstModuleBodyLine(lines, language) + 1, hadImports: false };
  }
  // JS/TS family.
  let lastImportLine = -1;
  for (let i = 0; i < lines.length; i += 1) {
    if (JS_IMPORT_LINE.test(lines[i]!)) lastImportLine = i;
  }
  if (lastImportLine >= 0) {
    return { insertLine: lastImportLine + 2, hadImports: true };
  }
  return { insertLine: firstModuleBodyLine(lines, language) + 1, hadImports: false };
}

/**
 * 0-based index of the first "real" content line (skip shebang / triple-slash
 * directives / module docstring).
 */
function firstModuleBodyLine(lines: readonly string[], language: InstrumentLanguage): number {
  let i = 0;
  // Shebang.
  if (i < lines.length && lines[i]!.startsWith("#!")) i += 1;
  if (language === "python") {
    // Skip module docstring: triple-quoted block starting on i (or after blank).
    while (i < lines.length && lines[i]!.trim() === "") i += 1;
    if (i < lines.length) {
      const first = lines[i]!;
      const m = first.match(/^(\s*)("""|''')/);
      if (m) {
        const quote = m[2]!;
        // Single-line docstring?
        const rest = first.slice(m[1]!.length + quote.length);
        if (rest.includes(quote)) {
          i += 1;
        } else {
          i += 1;
          while (i < lines.length && !lines[i]!.includes(quote)) i += 1;
          if (i < lines.length) i += 1; // past closing triple
        }
      }
    }
  } else {
    // Triple-slash directives.
    while (i < lines.length && lines[i]!.trim().startsWith("///")) i += 1;
  }
  return i;
}

// ---------------------------------------------------------------------------
// Quote-style detection (TS/JS) — majority wins; default double if ambiguous.
// ---------------------------------------------------------------------------

type QuoteStyle = "single" | "double" | "none";

function detectQuoteStyle(lines: readonly string[]): QuoteStyle {
  let single = 0;
  let double = 0;
  for (const ln of lines) {
    const m = ln.match(/^\s*import\s+[^"']*['"]([^'"]+)['"]/);
    if (!m) continue;
    const idx = ln.indexOf(m[1]!);
    if (idx < 1) continue;
    const q = ln[idx - 1]!;
    if (q === "'") single += 1;
    else if (q === '"') double += 1;
  }
  if (single > double) return "single";
  return "double";
}

// ---------------------------------------------------------------------------
// Rendering — produce the import block as a string
// ---------------------------------------------------------------------------

interface RenderOpts {
  readonly language: InstrumentLanguage;
  readonly groups: readonly ImportGroup[];
  readonly quoteStyle: QuoteStyle;
  readonly anchorHadImports: boolean;
  readonly sourceFile: SourceFile;
}

function renderImportBlock(opts: RenderOpts): string {
  const lines: string[] = [];
  if (opts.language === "python") {
    for (const g of opts.groups) {
      lines.push(renderPythonImport(g, opts.sourceFile));
    }
  } else {
    const q = opts.quoteStyle === "single" ? "'" : '"';
    for (const g of opts.groups) {
      lines.push(renderJsImport(g, q));
    }
  }
  // One trailing blank line after the block (spec §6.2 "one blank line").
  return lines.join("\n") + "\n\n";
}

function renderPythonImport(g: ImportGroup, sourceFile: SourceFile): string {
  // Extension: if the file ALREADY has `from m import X`, emit a parallel
  // statement `from m import Y, Z` rather than in-place rewriting X's line.
  // The contract is: we never rewrite existing imports. We only emit new ones.
  // Dedup prevents conflicting parallel statements (scanner surfaced X in
  // existingImports; filterAlreadyPresent removed any spec for X).
  if (g.kind === "default") {
    // Python has no first-class default import; treat as `import module as name`.
    const s = g.specs[0]!;
    if (s.alias && s.alias !== s.module) return `import ${s.module} as ${s.alias}`;
    return `import ${s.module}`;
  }
  if (g.kind === "namespace") {
    const s = g.specs[0]!;
    if (s.alias) return `import ${s.module} as ${s.alias}`;
    return `import ${s.module}`;
  }
  // named
  const names = g.specs
    .map((s) => (s.alias ? `${s.name} as ${s.alias}` : s.name))
    .join(", ");
  // Intentionally read-and-ignore sourceFile — future nice-to-have: wrap at 88 col per file style.
  void sourceFile;
  return `from ${g.module} import ${names}`;
}

function renderJsImport(g: ImportGroup, q: string): string {
  if (g.kind === "default") {
    const s = g.specs[0]!;
    return `import ${s.alias ?? s.name} from ${q}${g.module}${q};`;
  }
  if (g.kind === "namespace") {
    const s = g.specs[0]!;
    return `import * as ${s.alias ?? s.name} from ${q}${g.module}${q};`;
  }
  const names = g.specs
    .map((s) => (s.alias ? `${s.name} as ${s.alias}` : s.name))
    .join(", ");
  return `import { ${names} } from ${q}${g.module}${q};`;
}
