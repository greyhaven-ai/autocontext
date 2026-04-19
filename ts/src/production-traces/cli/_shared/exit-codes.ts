// Production-traces CLI exit-code contract.
//
// Matches spec §9.7 (Foundation A) and extends the range shared with Foundation B
// §6.5 so top-level CI workflows can reason about both tools uniformly:
//
//   0   success
//   1   domain failure (including operator error / invalid args)
//   2   partial success — advisory / marginal (e.g. ingest with per-line errors
//       that did NOT produce a hard-fail)
//   10+ system-class faults (lock timeout, I/O, missing inputs, schema drift)
//
// Production-traces-specific codes are numbered 10..14 per spec §9.7:
//
//   10 lock timeout               (shares semantics with Foundation B LOCK_TIMEOUT)
//   11 invalid config file        (e.g. malformed redaction-policy.json)
//   12 no matching traces         (e.g. `list --since <future>` returns nothing
//                                  and the CLI explicitly treats empty as an error)
//   13 schema version mismatch    (reading a trace / dataset from a newer incompatible
//                                  schema version)
//   14 I/O failure                (filesystem / permission problems)
//
// The shape mirrors `control-plane/cli/_shared/exit-codes.ts` exactly, so a
// small drift check in the test suite can keep the two tables in sync.

export const EXIT = {
  SUCCESS: 0,
  DOMAIN_FAILURE: 1,
  PARTIAL_SUCCESS: 2,

  LOCK_TIMEOUT: 10,
  INVALID_CONFIG: 11,
  NO_MATCHING_TRACES: 12,
  SCHEMA_VERSION_MISMATCH: 13,
  IO_FAILURE: 14,
} as const;

export type ExitCode = (typeof EXIT)[keyof typeof EXIT];
