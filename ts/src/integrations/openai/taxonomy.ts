/**
 * Exception → reason-key lookup with SDK-version-presence guards.
 *
 * Spec §4.3. Classes absent in older openai SDK versions fall through to
 * ``uncategorized``. Mirror of Python ``_taxonomy.py``.
 */
import {
  OPENAI_ERROR_REASONS,
  type OpenAiErrorReasonKey,
} from "../../production-traces/taxonomy/openai-error-reasons.js";

/**
 * Look up ``err``'s class name in the taxonomy; returns ``"uncategorized"`` on miss.
 * Mirrors Python ``map_exception_to_reason``.
 */
export function mapExceptionToReason(err: unknown): OpenAiErrorReasonKey {
  const name = (err as Error | null)?.constructor?.name;
  if (typeof name === "string" && name in OPENAI_ERROR_REASONS) {
    return OPENAI_ERROR_REASONS[name] as OpenAiErrorReasonKey;
  }
  return "uncategorized";
}

/**
 * Test helper — does the installed OpenAI SDK export the given class name?
 */
export function isMappedClassPresent(className: string): boolean {
  try {
    // Dynamic require to avoid top-level import side effects
    const openai = require("openai") as Record<string, unknown>;
    return className in openai;
  } catch {
    return false;
  }
}
