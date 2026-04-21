/**
 * Backward-compatibility adapter for DetectorPlugin.produce().
 *
 * A2-II-b widened produce() from `readonly EditDescriptor[]` to
 * `PluginProduceResult`. This adapter wraps a legacy produce() implementation
 * so third-party plugins that have not yet migrated can still register.
 *
 * In-tree fixture plugins are migrated directly (not via this adapter).
 * Reserve this adapter for documented third-party-plugin migration paths.
 */
import type { PluginProduceResult } from "./plugin-interface.js";

/**
 * Wrap a legacy produce() that returns `readonly EditDescriptor[]` so it
 * satisfies the new `PluginProduceResult` contract.
 */
export function adaptLegacyProduce(
  legacy: (m: any, f: any) => readonly any[],
): (m: any, f: any) => PluginProduceResult {
  return (m, f) => ({ edits: legacy(m, f), advisories: [] });
}
