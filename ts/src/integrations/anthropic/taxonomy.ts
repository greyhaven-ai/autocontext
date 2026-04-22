/**
 * Exception → reason-key lookup for Anthropic errors.
 * Uses constructor.name to map SDK error class names to taxonomy keys.
 * Mirror of Python _taxonomy.py for the Anthropic provider.
 */
import {
  ANTHROPIC_ERROR_REASONS,
  type AnthropicErrorReasonKey,
} from "../../production-traces/taxonomy/anthropic-error-reasons.js";

/**
 * Look up err's class name in the Anthropic taxonomy.
 * Returns "uncategorized" on miss or if exc is not an object.
 */
export function mapExceptionToReason(exc: unknown): AnthropicErrorReasonKey {
  if (exc == null || typeof exc !== "object") return "uncategorized";
  const name = (exc as { constructor?: { name?: string } }).constructor?.name;
  if (!name) return "uncategorized";
  return (ANTHROPIC_ERROR_REASONS[name] ?? "uncategorized") as AnthropicErrorReasonKey;
}
