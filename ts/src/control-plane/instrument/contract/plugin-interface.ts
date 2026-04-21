/**
 * A2-I contract layer — plugin interface, EditDescriptor ADT, SourceFile shape.
 *
 * Every name here comes verbatim from spec §3.4 (ubiquitous language) and §4
 * (contract layer) of `docs/superpowers/specs/2026-04-19-a2-i-autoctx-instrument-design.md`.
 *
 * This module has zero imports from sibling instrument/ sub-contexts; it is the
 * foundation every other instrument/ module depends on. It may import from
 * `control-plane/contract/` (reused brands like `ContentHash`) but never from
 * `production-traces/`, `registry/`, `promotion/`, `emit/`, or `actuators/`.
 */

import type { ContentHash } from "../../contract/branded-ids.js";

/** Languages the scanner + detectors support. Strict superset of the extension map in `file-type-filter.ts`. */
export type InstrumentLanguage = "python" | "typescript" | "javascript" | "jsx" | "tsx";

/** Directive semantics — one entry per source line keyed by its 1-based line number. */
export type DirectiveValue = "off" | "on" | "off-file" | "on-file";
export type DirectiveMap = ReadonlyMap<number, DirectiveValue>;

/** Detected indentation style for the enclosing file. */
export type IndentationStyle =
  | { readonly kind: "spaces"; readonly width: number }
  | { readonly kind: "tabs" };

/** One name imported from a module, optionally with a local alias. */
export interface ImportedName {
  /** The name exported from the module. */
  readonly name: string;
  /** Local binding if `import X as Y` / `from m import X as Y`. */
  readonly alias?: string;
}

/** Helper — does `names` contain an entry with `name` and no alias? */
export function hasImport(names: ReadonlySet<ImportedName>, name: string): boolean {
  for (const n of names) if (n.name === name && n.alias === undefined) return true;
  return false;
}

/** Helper — given a local identifier `localName`, return the source name if imported. */
export function resolveLocalName(
  names: ReadonlySet<ImportedName>,
  localName: string,
): string | undefined {
  for (const n of names) {
    if (n.alias === localName) return n.name;
    if (n.alias === undefined && n.name === localName) return n.name;
  }
  return undefined;
}

/** One existing import statement already present in the source file. */
export interface ExistingImport {
  readonly module: string;
  readonly names: ReadonlySet<ImportedName>;
}

/** Set of existing imports — a `ReadonlySet<ExistingImport>` so planner can efficiently dedupe. */
export type ImportSet = ReadonlySet<ExistingImport>;

/** Byte + line/col bounds for a contiguous source range. Monotonic invariant: startByte <= endByte. */
export interface SourceRange {
  readonly startByte: number;
  readonly endByte: number;
  readonly startLineCol: { readonly line: number; readonly col: number };
  readonly endLineCol: { readonly line: number; readonly col: number };
}

/** A single import the plugin requests be ensured present in the file post-patch. */
export interface ImportSpec {
  readonly module: string;
  readonly name: string;
  readonly alias?: string;
  readonly kind: "named" | "default" | "namespace";
}

/** Fields common to every EditDescriptor variant. `pluginId` + `sourceFilePath` are injected by the pipeline post-plugin. */
export interface BaseEdit {
  readonly pluginId: string;
  readonly sourceFilePath: string;
  readonly importsNeeded: readonly ImportSpec[];
  readonly notes?: readonly string[];
}

/** Wrap an expression at `range` with `wrapFn(...wrapArgsBefore, <expr>, ...wrapArgsAfter)`. */
export interface WrapExpressionEdit extends BaseEdit {
  readonly kind: "wrap-expression";
  readonly range: SourceRange;
  readonly wrapFn: string;
  readonly wrapArgsBefore?: readonly string[];
  readonly wrapArgsAfter?: readonly string[];
}

/** Insert a new statement immediately before or after `anchor.range`. */
export interface InsertStatementEdit extends BaseEdit {
  readonly kind: "insert-statement";
  readonly anchor: {
    readonly kind: "before" | "after";
    readonly range: SourceRange;
  };
  readonly statementSource: string;
}

/** Replace an expression at `range` with `replacementSource`. */
export interface ReplaceExpressionEdit extends BaseEdit {
  readonly kind: "replace-expression";
  readonly range: SourceRange;
  readonly replacementSource: string;
}

/** Discriminated union of every semantic edit a detector plugin may produce. */
export type EditDescriptor =
  | WrapExpressionEdit
  | InsertStatementEdit
  | ReplaceExpressionEdit;

/**
 * One match of a secret-literal pattern scanned against file bytes.
 *
 * Populated by `safety/secret-detector.ts#detectSecretLiterals`. The contract
 * layer owns the SHAPE because `SourceFile.secretMatches` surfaces this type
 * to planner + pr-body consumers — moving the shape into safety/ would force
 * contract → safety (forbidden by spec §3.3).
 *
 * Fields:
 *  - `pattern` — detector-supplied id (e.g., "aws-access-key"). Stable across runs.
 *  - `byteOffset` — 0-based byte offset of the match start in the file bytes.
 *  - `lineNumber` — 1-based line of the match start.
 *  - `excerpt` — short printable string for error messages; may be truncated/redacted.
 */
export interface SecretMatch {
  readonly pattern: string;
  readonly byteOffset: number;
  readonly lineNumber: number;
  readonly excerpt: string;
}

/**
 * One customer source file as seen by scanner + plugins.
 *
 * `tree` is `unknown` at the contract boundary so instrument/contract stays free of the
 * tree-sitter Node FFI dependency. The scanner narrows to a real TreeSitterTree internally;
 * plugins that need to walk the CST cast via `import type` from their own SDK boundary.
 *
 * Lazy tree access: `tree` is intentionally a getter on the scanner wrapper (see
 * `scanner/source-file.ts`). Multiple reads return the same cached tree.
 */
export interface SourceFile {
  readonly path: string;
  readonly language: InstrumentLanguage;
  readonly bytes: Buffer;
  /** Lazy tree-sitter CST. Parsed on first access. Scanner narrows to Parser.Tree at runtime. */
  readonly tree: unknown;
  readonly directives: DirectiveMap;
  readonly hasSecretLiteral: boolean;
  /**
   * Secret matches found at load time (Layer 3). Empty when `hasSecretLiteral`
   * is `false`. Planner surfaces these in per-file refuse diagnostics.
   */
  readonly secretMatches: readonly SecretMatch[];
  readonly existingImports: ImportSet;
  readonly indentationStyle: IndentationStyle;
}

/**
 * An advisory emitted by a plugin when it decides NOT to wrap a call site,
 * describing why and giving the user actionable information.
 */
export interface PluginAdvisory {
  readonly pluginId: string;
  readonly sourceFilePath: string;
  readonly range: SourceRange;
  readonly kind:
    | "unresolved-import"
    | "factoryFunction"
    | "deferred-sdk-variant"
    | "already-wrapped";
  readonly reason: string;
}

/** The full result returned by `DetectorPlugin.produce()`. */
export interface PluginProduceResult {
  readonly edits: readonly EditDescriptor[];
  readonly advisories: readonly PluginAdvisory[];
}

/**
 * Detector plugin contract. Plugins are registered via `registerDetectorPlugin(plugin)`
 * (Layer 4 — `registry/plugin-registry.ts`).
 */
export interface DetectorPlugin {
  readonly id: string;
  readonly supports: {
    readonly language: InstrumentLanguage;
    readonly sdkName: string;
  };
  readonly treeSitterQueries: readonly string[];
  produce(match: TreeSitterMatch, sourceFile: SourceFile): PluginProduceResult;
}

/** Opaque tree-sitter query match handed to plugins; narrowed per-plugin as needed. */
export interface TreeSitterMatch {
  readonly captures: ReadonlyArray<{ readonly name: string; readonly node: { readonly startIndex: number; readonly endIndex: number } }>;
}

// --------------------------------------------------------------------------
// Session + plan envelopes (spec §9.1 + §9.2). Types mirror the JSON Schemas
// under `./json-schemas/`. Schema and type definitions are kept in lock-step
// by the validators module (schema-type drift would be caught at validator
// compile time via the `_TypeCheck` type-assertion pattern).
// --------------------------------------------------------------------------

/** Snapshot of one `autoctx instrument` invocation. Non-deterministic (ULID + timestamps). */
export interface InstrumentSession {
  readonly cwd: string;
  readonly flags: InstrumentFlagsSnapshot;
  readonly startedAt: string;
  readonly endedAt: string;
  readonly autoctxVersion: string;
  readonly registeredPlugins: readonly {
    readonly id: string;
    readonly version: string;
    readonly sdkName: string;
    readonly language: InstrumentLanguage;
  }[];
  readonly gitignoreFingerprint: ContentHash;
}

/** Verbatim snapshot of the CLI flags as a session was launched with. */
export interface InstrumentFlagsSnapshot {
  readonly mode: "dry-run" | "apply" | "apply-branch";
  readonly branch?: string;
  readonly commit?: string;
  readonly enhanced: boolean;
  readonly maxFileBytes: number;
  readonly failIfEmpty: boolean;
  readonly excludes: readonly string[];
  readonly excludeFrom?: string;
  readonly output: "json" | "table" | "pretty";
  readonly force: boolean;
}

/** Per-file metadata captured during scan (subset of SourceFile safe to serialize). */
export interface PlanSourceFileMetadata {
  readonly path: string;
  readonly language: InstrumentLanguage;
  readonly directivesSummary: {
    readonly offLines: readonly number[];
    readonly offFileAtLine?: number;
  };
  readonly hasSecretLiteral: boolean;
  readonly existingImports: readonly { readonly module: string; readonly names: readonly string[] }[];
}

export type ConflictDecision =
  | { readonly kind: "accepted" }
  | { readonly kind: "deduplicated"; readonly reason: string }
  | { readonly kind: "rejected-conflict"; readonly conflictingPluginIds: readonly string[]; readonly reason: string };

export type SafetyDecision =
  | { readonly kind: "allow" }
  | { readonly kind: "refuse"; readonly reason: string };

/**
 * Composed pre-patch plan. **Byte-deterministic** given the same inputs — reused as
 * the CI drift-detection fingerprint (see spec §9.4).
 */
export interface InstrumentPlan {
  readonly schemaVersion: string;
  readonly edits: readonly EditDescriptor[];
  readonly sourceFiles: readonly PlanSourceFileMetadata[];
  readonly conflictDecisions: readonly {
    readonly filePath: string;
    readonly decision: ConflictDecision;
  }[];
  readonly safetyDecisions: readonly {
    readonly filePath: string;
    readonly decision: SafetyDecision;
  }[];
}
