/**
 * A2-I scanner — lazy tree-sitter grammar loading.
 *
 * Spec §5.2: grammars are loaded on first parse attempt (NOT at module-init
 * time). Once loaded, the Parser instance is cached per language and reused.
 *
 * The tree-sitter native bindings are loaded via dynamic `import()` so that
 * no grammar package is touched unless a file of that language is actually
 * parsed. This keeps the scanner cheap for repos that never see a particular
 * language.
 *
 * All direct interaction with `tree-sitter` is confined to this module — the
 * rest of the scanner (and the instrument/ contract) stays free of the FFI
 * boundary.
 */
import type { InstrumentLanguage } from "../contract/plugin-interface.js";

// Parser and Tree are structurally typed here — the actual FFI types come from
// the `tree-sitter` package's own .d.ts. We import the type lazily from a cast
// to keep module-init free of the native binding.
// The `any` casts inside this file are the A2-I "FFI boundary" budget bumps
// noted in spec §11.8.
/* eslint-disable @typescript-eslint/no-explicit-any */

export interface LoadedParser {
  readonly language: InstrumentLanguage;
  /** Raw tree-sitter Parser instance with the language set. */
  readonly parser: any;
}

export interface TreeSitterTree {
  readonly rootNode: unknown;
}

type GrammarLoader = () => Promise<unknown>;

// Spec §5.2: the exact mapping. Keys are InstrumentLanguage; TypeScript and TSX
// share a single package but expose different language objects.
const GRAMMAR_LOADERS: Record<InstrumentLanguage, GrammarLoader> = {
  python: () => import("tree-sitter-python").then((m) => (m as any).default ?? m),
  typescript: () => import("tree-sitter-typescript").then((m) => ((m as any).default ?? m).typescript),
  tsx: () => import("tree-sitter-typescript").then((m) => ((m as any).default ?? m).tsx),
  javascript: () => import("tree-sitter-javascript").then((m) => (m as any).default ?? m),
  // JSX support in tree-sitter-javascript is automatic; same grammar handles both.
  jsx: () => import("tree-sitter-javascript").then((m) => (m as any).default ?? m),
};

// Module-level parser cache — one Parser per language.
const parserCache = new Map<InstrumentLanguage, LoadedParser>();
// Track grammars that have been loaded for observability (used by tests to
// confirm we didn't preload grammars we never needed).
const grammarsLoaded = new Set<InstrumentLanguage>();

/**
 * Load a Parser instance for `language`. Cached.
 *
 * Does NOT throw on first-call module-init miss — resolves lazily the first time
 * a file of that language is parsed. Subsequent calls reuse the cached parser.
 */
export async function loadParser(language: InstrumentLanguage): Promise<LoadedParser> {
  const cached = parserCache.get(language);
  if (cached) return cached;

  // Import the tree-sitter runtime lazily. Keeping the require() / import() at
  // call time ensures no native binding loads unless someone actually parses.
  const treeSitterModule: any = await import("tree-sitter");
  const ParserCtor: any = treeSitterModule.default ?? treeSitterModule;
  const parser = new ParserCtor();

  const grammar = await GRAMMAR_LOADERS[language]();
  parser.setLanguage(grammar);
  grammarsLoaded.add(language);

  const loaded: LoadedParser = { language, parser };
  parserCache.set(language, loaded);
  return loaded;
}

/**
 * Parse `bytes` (source text) with the cached Parser for `language`. Returns a
 * TreeSitterTree wrapper. `language` is loaded lazily on first use.
 */
export async function parseSource(language: InstrumentLanguage, bytes: Buffer | string): Promise<TreeSitterTree> {
  const { parser } = await loadParser(language);
  const source = typeof bytes === "string" ? bytes : bytes.toString("utf-8");
  const tree = parser.parse(source);
  return tree as TreeSitterTree;
}

/**
 * Test + diagnostics helper. Tells you which grammars have actually been loaded
 * so far this process. Used to verify lazy loading behavior.
 */
export function loadedGrammarsSnapshot(): ReadonlySet<InstrumentLanguage> {
  return new Set(grammarsLoaded);
}

/** Test helper — reset caches. Not exported from the barrel. */
export function __resetForTests(): void {
  parserCache.clear();
  grammarsLoaded.clear();
}
