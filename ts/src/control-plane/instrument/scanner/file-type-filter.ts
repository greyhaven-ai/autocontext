/**
 * A2-I scanner — extension → InstrumentLanguage mapping.
 *
 * Spec §5.1 item 4: extension filter keeps `.py`, `.js`, `.jsx`, `.mjs`, `.cjs`,
 * `.ts`, `.tsx`, `.mts`, `.cts` and rejects everything else.
 *
 * Pure predicate. Zero I/O. No tree-sitter dependency.
 */
import type { InstrumentLanguage } from "../contract/plugin-interface.js";

/**
 * Map a path's lowercase extension to an InstrumentLanguage, or null if unsupported.
 * Matches the final `.ext` segment; does not peek inside files or inspect shebangs.
 */
export function languageFromPath(path: string): InstrumentLanguage | null {
  // Find the last '.' after the last '/' so 'server.config.d.ts' treats as '.ts'.
  const lastSlash = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  const basename = lastSlash === -1 ? path : path.slice(lastSlash + 1);
  const dotIdx = basename.lastIndexOf(".");
  if (dotIdx <= 0) return null; // hidden files like '.env' have no extension we want
  const ext = basename.slice(dotIdx).toLowerCase();

  switch (ext) {
    case ".py":
      return "python";
    case ".ts":
    case ".mts":
    case ".cts":
      return "typescript";
    case ".tsx":
      return "tsx";
    case ".js":
    case ".mjs":
    case ".cjs":
      return "javascript";
    case ".jsx":
      return "jsx";
    default:
      return null;
  }
}

/** Convenience predicate — used by the walker as a fast early-reject gate. */
export function isSupportedPath(path: string): boolean {
  return languageFromPath(path) !== null;
}
