/**
 * Anthropic exception class → `outcome.error.type` taxonomy (TS half).
 *
 * Cross-runtime parity: the Python counterpart at
 * `autocontext/src/autocontext/production_traces/taxonomy/anthropic_error_reasons.py`
 * MUST have the same keys + values. Parity tests keep the two in lock-step.
 */

export type AnthropicErrorReasonKey =
  | "rateLimited"
  | "timeout"
  | "badRequest"
  | "authentication"
  | "permissionDenied"
  | "notFound"
  | "apiConnection"
  | "overloaded"
  | "upstreamError"
  | "uncategorized";

export const ANTHROPIC_ERROR_REASONS: Readonly<
  Record<string, AnthropicErrorReasonKey>
> = Object.freeze({
  RateLimitError: "rateLimited",
  APITimeoutError: "timeout",
  BadRequestError: "badRequest",
  AuthenticationError: "authentication",
  PermissionDeniedError: "permissionDenied",
  NotFoundError: "notFound",
  APIConnectionError: "apiConnection",
  OverloadedError: "overloaded",
  ConflictError: "upstreamError",
  UnprocessableEntityError: "upstreamError",
  InternalServerError: "upstreamError",
  APIStatusError: "upstreamError",
  APIError: "upstreamError",
});

export const ANTHROPIC_ERROR_REASON_KEYS: readonly AnthropicErrorReasonKey[] =
  Object.freeze([
    "rateLimited",
    "timeout",
    "badRequest",
    "authentication",
    "permissionDenied",
    "notFound",
    "apiConnection",
    "overloaded",
    "upstreamError",
    "uncategorized",
  ]);
