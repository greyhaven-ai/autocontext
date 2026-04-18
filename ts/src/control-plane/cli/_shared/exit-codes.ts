// Control-plane CLI exit-code contract (spec §6.5 — CI-facing).
//
// 0–9  : user-level decision signals (promotion outcome). A CI workflow treats
//        0/2 as "continue" (with possibly different lanes) and 1 as "fail".
// 10+  : system errors (tool-side problems, not decision outcomes). CI should
//        treat any 10+ as a retryable infrastructure fault distinct from a
//        hard-fail decision.

export const EXIT = {
  PASS_STRONG_OR_MODERATE: 0,
  HARD_FAIL: 1,
  MARGINAL: 2,

  LOCK_TIMEOUT: 10,
  MISSING_BASELINE: 11,
  INVALID_ARTIFACT: 12,
  SCHEMA_VERSION_MISMATCH: 13,
  CASCADE_ROLLBACK_REQUIRED: 14,
  VALIDATION_FAILED: 15,
  NOT_IMPLEMENTED: 16,
  IO_ERROR: 17,
  UNKNOWN_ACTUATOR: 18,
} as const;

export type ExitCode = (typeof EXIT)[keyof typeof EXIT];
