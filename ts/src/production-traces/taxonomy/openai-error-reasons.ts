/**
 * OpenAI exception class → `outcome.error.type` taxonomy (TS half).
 *
 * Cross-runtime parity: the Python counterpart at
 * `autocontext/src/autocontext/production_traces/taxonomy/openai_error_reasons.py`
 * MUST have the same keys + values. Parity tests keep the two in lock-step.
 *
 * Keys are stored as class *names* (strings) rather than imported classes so
 * the table stays importable across OpenAI SDK version boundaries — a class
 * missing from the installed SDK falls through to `uncategorized` at
 * runtime-mapping time.
 */

export type OpenAiErrorReasonKey =
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
  | "uncategorized";

export const OPENAI_ERROR_REASONS: Readonly<Record<string, OpenAiErrorReasonKey>> =
  Object.freeze({
    RateLimitError: "rateLimited",
    APITimeoutError: "timeout",
    BadRequestError: "badRequest",
    AuthenticationError: "authentication",
    PermissionDeniedError: "permissionDenied",
    NotFoundError: "notFound",
    APIConnectionError: "apiConnection",
    ContentFilterFinishReasonError: "contentFilter",
    LengthFinishReasonError: "lengthCap",
    UnprocessableEntityError: "upstreamError",
    ConflictError: "upstreamError",
    APIError: "upstreamError",
  });

export const OPENAI_ERROR_REASON_KEYS: readonly OpenAiErrorReasonKey[] =
  Object.freeze([
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
    "uncategorized",
  ]);
