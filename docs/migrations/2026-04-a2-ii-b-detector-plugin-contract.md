# Migration: `DetectorPlugin.produce()` widening + `ExistingImport` alias preservation

**Released in:** A2-II-b (branch `a2-ii-b-openai-integration`)
**Affects:** Third-party `DetectorPlugin` implementations (none shipped yet; documented here for future plugin authors)
**Severity:** Minor — existing plugins compile and run unchanged; no action required unless you want to opt in to alias-aware import resolution.

---

## Background

A2-II-b introduced two backward-compatible widening changes to the A2-I plugin contract
(`ts/src/control-plane/instrument/contract/plugin-interface.ts`):

1. **`DetectorPlugin.produce()` return type widened** — from an internal shape to the
   exported `PluginProduceResult` interface, which requires both `edits` and `advisories`.
   Previously the scanner accepted partial return values; now `advisories` is required
   (though it may be an empty array).

2. **`ExistingImport.names` now preserves aliases** — the `names` field was previously
   typed as `ReadonlySet<string>` (name-only). It is now `ReadonlySet<ImportedName>`,
   where `ImportedName` is `{ name: string; alias?: string }`. The scanner captures the
   `as`-alias from `from openai import OpenAI as OAI` / `import { OpenAI as OAI } from "openai"`
   and stores it in `alias`. Plugins that only check for the canonical name still work via
   the provided `resolveLocalName` helper.

---

## Change 1: `PluginProduceResult` — `advisories` now required

### Before (A2-II-a and earlier)

The scanner accepted any object shape from `produce()`. A minimal plugin might return:

```typescript
// Old plugin (compiled fine, advisories silently dropped)
import type { DetectorPlugin, PluginProduceResult, SourceFile, TreeSitterMatch } from "autoctx/control-plane/instrument";

export const plugin: DetectorPlugin = {
  id: "@example/detector-foo",
  supports: { language: "python", sdkName: "foo" },
  treeSitterQueries: [`(call function: (identifier) @id (#eq? @id "Foo"))`],
  produce(match: TreeSitterMatch, sourceFile: SourceFile): PluginProduceResult {
    // Returned only edits — no advisories field.
    return { edits: [/* ... */] } as unknown as PluginProduceResult;
  },
};
```

### After (A2-II-b)

`PluginProduceResult` is a proper interface with both fields required:

```typescript
export interface PluginProduceResult {
  readonly edits: readonly EditDescriptor[];
  readonly advisories: readonly PluginAdvisory[];
}
```

A minimal compliant plugin:

```typescript
import type { DetectorPlugin, PluginProduceResult, SourceFile, TreeSitterMatch } from "autoctx/control-plane/instrument";

export const plugin: DetectorPlugin = {
  id: "@example/detector-foo",
  supports: { language: "python", sdkName: "foo" },
  treeSitterQueries: [`(call function: (identifier) @id (#eq? @id "Foo"))`],
  produce(match: TreeSitterMatch, sourceFile: SourceFile): PluginProduceResult {
    return {
      edits: [/* ... */],
      advisories: [],  // ← now required; empty array is valid
    };
  },
};
```

**Migration action**: Add `advisories: []` to any `produce()` return value that
previously omitted the field. TypeScript will raise a compile error if you miss it.

---

## Change 2: `ExistingImport.names` — alias preservation

### Before (A2-II-a and earlier)

`ExistingImport.names` was typed as `ReadonlySet<string>`. Plugin code did a simple
set membership check:

```typescript
// Old: names was ReadonlySet<string>
function hasOpenAI(imports: readonly ExistingImport[]): boolean {
  return imports.some(
    (imp) => imp.module === "openai" && imp.names.has("OpenAI")
  );
}
```

This worked for canonical imports (`from openai import OpenAI`) but silently
missed aliased imports (`from openai import OpenAI as OAI`) — `names.has("OpenAI")`
returned `true` for the canonical form, but the local binding in the file was `OAI`.

### After (A2-II-b)

`ExistingImport.names` is now `ReadonlySet<ImportedName>`:

```typescript
export interface ImportedName {
  readonly name: string;   // The name as exported from the module
  readonly alias?: string; // Local binding if imported as an alias
}

export interface ExistingImport {
  readonly module: string;
  readonly names: ReadonlySet<ImportedName>;
}
```

The scanner populates `alias` when it sees `from openai import OpenAI as OAI`.
A plugin that needs to find the **local binding** (e.g., to match it in a tree-sitter
capture) should use the provided helpers:

```typescript
import {
  hasImport,
  resolveLocalName,
  type ExistingImport,
} from "autoctx/control-plane/instrument";

// Check: does the file import "OpenAI" from "openai" (canonical, no alias)?
function hasCanonicalOpenAI(imports: readonly ExistingImport[]): boolean {
  return imports.some(
    (imp) => imp.module === "openai" && hasImport(imp.names, "OpenAI")
  );
}

// Resolve: given a local identifier seen in a capture, what was the source name?
function resolveCapture(imports: readonly ExistingImport[], localName: string): string | undefined {
  for (const imp of imports) {
    const sourceName = resolveLocalName(imp.names, localName);
    if (sourceName !== undefined) return sourceName;
  }
  return undefined;
}

// Full alias-aware check (handles OpenAI, OAI, aliased.OpenAI, etc.):
function localNameForOpenAI(imports: readonly ExistingImport[]): string | undefined {
  for (const imp of imports) {
    if (imp.module !== "openai") continue;
    for (const entry of imp.names) {
      if (entry.name === "OpenAI") return entry.alias ?? entry.name;
    }
  }
  return undefined;
}
```

**Migration action**: If your plugin previously called `imp.names.has("OpenAI")`,
replace that with `hasImport(imp.names, "OpenAI")` (for canonical-only) or iterate
`imp.names` to check `entry.name === "OpenAI"` (for alias-aware). TypeScript will
raise a compile error on `imp.names.has(string)` since `Set<ImportedName>` no longer
accepts a bare string.

---

## In-tree migrations performed by A2-II-b

All fixture plugins and mock implementations inside this repository have been migrated:

- `ts/tests/instrument/` — fixture plugins updated to return `{ edits, advisories }`.
- `ts/src/control-plane/instrument/detectors/openai-python/plugin.ts` — uses `resolveLocalName`.
- `ts/src/control-plane/instrument/detectors/openai-ts/plugin.ts` — uses `resolveLocalName`.

---

## Summary table

| Item | Before | After | Action required |
|------|--------|-------|-----------------|
| `produce()` return `advisories` | optional / missing | required `readonly PluginAdvisory[]` | Add `advisories: []` |
| `ExistingImport.names` element type | `string` | `ImportedName` (`{ name, alias? }`) | Replace `.has(string)` with `hasImport()` or iterate |
| `resolveLocalName` helper | not exported | exported from plugin-interface | Use for local-identifier resolution |
| `hasImport` helper | not exported | exported from plugin-interface | Use for canonical name checks |
