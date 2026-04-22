export {
  OPENAI_ERROR_REASONS,
  OPENAI_ERROR_REASON_KEYS,
  type OpenAiErrorReasonKey,
} from "./openai-error-reasons.js";

export {
  ANTHROPIC_ERROR_REASONS,
  ANTHROPIC_ERROR_REASON_KEYS,
  type AnthropicErrorReasonKey,
} from "./anthropic-error-reasons.js";

export type OutcomeReasonKey =
  | "rateLimited"
  | "timeout"
  | "badRequest"
  | "authentication"
  | "permissionDenied"
  | "notFound"
  | "apiConnection"
  | "contentFilter"
  | "lengthCap"
  | "upstreamError"
  | "overloaded"
  | "uncategorized";

export const OUTCOME_REASON_KEYS: readonly OutcomeReasonKey[] = Object.freeze([
  "rateLimited",
  "timeout",
  "badRequest",
  "authentication",
  "permissionDenied",
  "notFound",
  "apiConnection",
  "contentFilter",
  "lengthCap",
  "upstreamError",
  "overloaded",
  "uncategorized",
]);
