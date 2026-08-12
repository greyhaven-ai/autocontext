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
  "claude-fable-5": 128_000,
  "claude-opus-5": 128_000,
  "claude-sonnet-5": 128_000,
  "claude-3-haiku": 4096,
  "claude-3-opus": 4096,
  "claude-3-sonnet": 4096,
  "claude-3-5-haiku": 8192,
  "claude-3-5-sonnet": 8192,
  "gpt-5.6-luna": 128_000,
  "gpt-5.6-sol": 128_000,
  "gpt-5.6-terra": 128_000,
};

/** min(requested, known hard cap); unknown or absent models pass through. */
export function clampOutputTokens(requested: number, model: string | null | undefined): number {
  if (!model) return requested;
  const modelId = /^(?:anthropic|openai)\//.test(model) ? model.slice(model.indexOf("/") + 1) : model;
  let bestPrefix = "";
  for (const prefix of Object.keys(KNOWN_OUTPUT_CAPS)) {
    if (modelId.startsWith(prefix) && prefix.length > bestPrefix.length) {
      bestPrefix = prefix;
    }
  }
  if (!bestPrefix) return requested;
  return Math.min(requested, KNOWN_OUTPUT_CAPS[bestPrefix]);
}
