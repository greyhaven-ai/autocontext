/**
 * Model-aware output-token clamp (AC-905).
 * Mirrors Python's autocontext/providers/token_caps.py.
 *
 * A requested output budget must never exceed a model's known hard API
 * limit. The catalog lists only KNOWN hard caps; unknown models pass
 * through unclamped, because a stale catalog silently shrinking budgets is
 * worse than an occasional over-ask the provider rejects loudly.
 */

// known hard output-token limits by model-id prefix (longest prefix wins)
export const KNOWN_OUTPUT_CAPS: Record<string, number> = {
  "claude-3-haiku": 4096,
  "claude-3-opus": 4096,
  "claude-3-sonnet": 4096,
  "claude-3-5-haiku": 8192,
  "claude-3-5-sonnet": 8192,
};

/** min(requested, known hard cap); unknown or absent models pass through. */
export function clampOutputTokens(requested: number, model: string | null | undefined): number {
  if (!model) return requested;
  let bestPrefix = "";
  for (const prefix of Object.keys(KNOWN_OUTPUT_CAPS)) {
    if (model.startsWith(prefix) && prefix.length > bestPrefix.length) {
      bestPrefix = prefix;
    }
  }
  if (!bestPrefix) return requested;
  return Math.min(requested, KNOWN_OUTPUT_CAPS[bestPrefix]);
}
