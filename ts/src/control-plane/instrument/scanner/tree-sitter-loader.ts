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
 *
 * A2-II-b additions:
 *   - `ensureParserLoaded(language)` — async preload for the orchestrator's
 *     pre-loop phase; after this call `parseSync` is safe.
 *   - `parseSync(language, bytes)` — synchronous parse (errors if parser not
 *     yet loaded).
 *   - `loadQuery(language, queryString)` — compile and cache a tree-sitter
 *     Query object keyed by `${language}|${queryString}`.
 */
import type { InstrumentLanguage } from "../contract/plugin-interface.js";

// Parser and Tree are structurally typed here — the actual FFI types come from
// the `tree-sitter` package's own .d.ts. We import the type lazily from a cast
// to keep module-init free of the native binding.
// The `any` casts inside this file are the A2-I "FFI boundary" budget bumps
// noted in spec §11.8.
// A2-II-b adds ~12 additional `any` casts for the Query constructor FFI.
/* eslint-disable @typescript-eslint/no-explicit-any */

export interface LoadedParser {
  readonly language: InstrumentLanguage;
  /** Raw tree-sitter Parser instance with the language set. */
  readonly parser: any;
  /** Raw tree-sitter language object (needed for Query constructor). */
  readonly grammar: any;
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

// Module-level query cache — keyed by `${language}|${queryString}`.
// Value is the compiled tree-sitter Query object (typed as `unknown` to keep
// the FFI boundary inside this file; callers use `loadQuery` which returns
// `unknown` and the orchestrator casts only when calling `.matches()`).
const queryCache = new Map<string, unknown>();

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

  const loaded: LoadedParser = { language, parser, grammar };
  parserCache.set(language, loaded);
  return loaded;
}

/**
 * Async preload: ensure the parser for `language` is loaded and cached so
 * that subsequent `parseSync` calls succeed synchronously.
 *
 * The orchestrator calls this once per language before the file loop.
 */
export async function ensureParserLoaded(language: InstrumentLanguage): Promise<void> {
  await loadParser(language);
}

/**
 * Synchronous parse. Requires `ensureParserLoaded(language)` to have been
 * awaited first; throws if the parser is not yet cached.
 */
export function parseSync(language: InstrumentLanguage, bytes: Buffer | string): TreeSitterTree {
  const cached = parserCache.get(language);
  if (!cached) {
    throw new Error(
      `parseSync: parser for "${language}" not yet loaded. ` +
        `Call ensureParserLoaded("${language}") before the file loop.`,
    );
  }
  const source = typeof bytes === "string" ? bytes : bytes.toString("utf-8");
  const tree = cached.parser.parse(source);
  return tree as TreeSitterTree;
}

/**
 * Compile and cache a tree-sitter Query for `language` from `queryString`.
 *
 * Requires the parser (and therefore grammar) to already be loaded via
 * `ensureParserLoaded` — this is always called by the orchestrator's preload
 * phase before loadQuery is invoked.
 *
 * Cache key: `${language}|${queryString}`. Compilation is expensive; this
 * amortises it across all files of the same language.
 *
 * Returns the compiled Query as `unknown` to keep the FFI type out of callers.
 * The orchestrator casts to `any` only when invoking `.matches()`.
 */
export async function loadQuery(language: InstrumentLanguage, queryString: string): Promise<unknown> {
  const cacheKey = `${language}|${queryString}`;
  const cached = queryCache.get(cacheKey);
  if (cached !== undefined) return cached;

  // Ensure parser (and grammar) is loaded.
  const loaded = await loadParser(language);

  // Import the tree-sitter runtime to access the Query constructor.
  const treeSitterModule: any = await import("tree-sitter");
  const ParserCtor: any = treeSitterModule.default ?? treeSitterModule;
  const QueryCtor: any = ParserCtor.Query;

  const compiled = new QueryCtor(loaded.grammar, queryString);
  queryCache.set(cacheKey, compiled);
  return compiled;
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
 * Synchronous cache lookup for a previously compiled Query.
 *
 * Returns the cached Query object, or `undefined` if not yet compiled.
 * The orchestrator calls this inside the synchronous file-loop AFTER
 * `preloadParsersAndQueries` has already called `loadQuery` for every
 * relevant `(language, queryString)` pair.
 *
 * Returns `unknown` to keep the FFI type inside this module; callers cast.
 */
export function loadQuerySync(language: InstrumentLanguage, queryString: string): unknown {
  return queryCache.get(`${language}|${queryString}`);
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
  queryCache.clear();
}
