/**
 * A2-I Layer 4 — plugin registry.
 *
 * Spec §7.2: A2-I ships the tool inert. Integration libraries (A2-II+) call
 * `registerDetectorPlugin(plugin)` at import time to contribute SDK-specific
 * detectors.
 *
 * Invariants (spec §4.4):
 *   - I1 (duplicate-id is build-error) — `registerDetectorPlugin` throws on
 *     a repeat of `plugin.id`.
 *   - One plugin per (language, sdkName) pair — duplicate throws.
 *
 * The registry is process-global module state. `resetRegistryForTests` is a
 * test-only helper (beforeEach pattern) that returns the registry to empty.
 *
 * Import discipline (spec §3.3):
 *   - Imports `contract/` for types only. No scanner, safety, planner, or
 *     pipeline imports — the registry is tiny and orthogonal.
 */
import type {
  DetectorPlugin,
  InstrumentLanguage,
} from "../contract/plugin-interface.js";

// -----------------------------------------------------------------------------
// Module-global state. Two indices so both uniqueness checks are O(1):
//   - byId        : plugin.id → plugin
//   - byPair      : `${language}|${sdkName}` → plugin
//   - byLanguage  : language → plugin[] (insertion-ordered for reproducibility)
// -----------------------------------------------------------------------------

const byId = new Map<string, DetectorPlugin>();
const byPair = new Map<string, DetectorPlugin>();
const byLanguage = new Map<InstrumentLanguage, DetectorPlugin[]>();

function pairKey(language: InstrumentLanguage, sdkName: string): string {
  return `${language}|${sdkName}`;
}

/**
 * Register `plugin`. Throws if its `id` already exists OR if another plugin
 * has already registered for the same `(language, sdkName)` pair.
 *
 * The throw-first approach enforces invariant I1 without any silent-last-wins
 * surprises: the first loader wins; the second is a bug the caller must fix.
 */
export function registerDetectorPlugin(plugin: DetectorPlugin): void {
  if (byId.has(plugin.id)) {
    throw new Error(
      `duplicate plugin id "${plugin.id}" — another plugin with this id is already registered. ` +
        `Each DetectorPlugin.id must be globally unique (spec §4.4 I1).`,
    );
  }
  const key = pairKey(plugin.supports.language, plugin.supports.sdkName);
  if (byPair.has(key)) {
    const existing = byPair.get(key)!;
    throw new Error(
      `duplicate plugin for (${plugin.supports.language}, ${plugin.supports.sdkName}): ` +
        `"${plugin.id}" conflicts with already-registered "${existing.id}". ` +
        `At most one DetectorPlugin per (language, sdkName) pair (spec §4.1).`,
    );
  }

  byId.set(plugin.id, plugin);
  byPair.set(key, plugin);
  const list = byLanguage.get(plugin.supports.language) ?? [];
  list.push(plugin);
  byLanguage.set(plugin.supports.language, list);
}

/**
 * Return all plugins registered for `language`, in insertion order. Empty
 * array when no plugin has been registered for the language (including the
 * A2-I default — zero plugins registered).
 */
export function pluginsForLanguage(
  language: InstrumentLanguage,
): readonly DetectorPlugin[] {
  const list = byLanguage.get(language);
  if (!list) return [];
  // Defensive copy so callers can't mutate internal state.
  return list.slice();
}

/**
 * Test-only helper: clear all registered plugins. NEVER call from production
 * code. Tests use this in `beforeEach` to isolate registrations across cases.
 */
export function resetRegistryForTests(): void {
  byId.clear();
  byPair.clear();
  byLanguage.clear();
}
