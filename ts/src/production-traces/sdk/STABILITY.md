# autoctx/production-traces — API stability commitment

This document describes the stability guarantees for the
`autoctx/production-traces` subpath export.

## Versioning

The SDK versions in lock-step with the `autoctx` npm package. Customers pin
`autoctx@^0.4.0` (or whichever minor is current) and receive the SDK at the
same version. `[SDK]` markers on CHANGELOG entries let you filter on
SDK-relevant changes.

## Compatibility promise

### Patch releases (e.g. `0.4.3` -> `0.4.4`)

* No signature changes.
* No removals.
* No behavioral changes visible to correctly-typed callers.

### Minor releases (e.g. `0.4.x` -> `0.5.0`)

* Additive changes only: new exports, new optional arguments, new struct
  fields with safe defaults.
* Signature changes to existing exports: NOT permitted.
* Removals: NOT permitted.

### Major releases (e.g. `0.x` -> `1.0`)

* Signature changes and removals are permitted.
* Each removal has a one-minor deprecation window. Deprecated exports
  print a runtime warning in development and a TypeScript deprecation
  marker at compile time.
* The schema version (on-disk `ProductionTrace.schemaVersion`) is
  versioned independently of the JS-surface major and follows its own
  compatibility rules documented in the production-traces contract.

## Surface included in the commitment

The stable surface is everything exported from
`autoctx/production-traces`:

* Functions: `buildTrace`, `writeJsonl`, `hashUserId`, `hashSessionId`,
  `loadInstallSalt`, `initializeInstallSalt`, `rotateInstallSalt`,
  `validateProductionTrace`, `validateProductionTraceDict`.
* Classes: `TraceBatch`, `ValidationError`.
* Type aliases and interfaces exported from the barrel.
* The on-disk JSONL format produced by `writeJsonl` (path layout, line
  ending, canonical JSON serialization).

## Surface NOT in the commitment

* The HTTP/CLI ingest commands under `autoctx` binary — those follow
  their own compatibility policy.
* Internal modules under `src/production-traces/sdk/` reachable only
  via relative imports.
* Any export not re-exported from `sdk/index.ts`.

## Cross-runtime parity

The SDK maintains byte-for-byte canonical-JSON parity with the Python
emit SDK (`autocontext.production_traces.emit.build_trace`) for every
input both SDKs accept. Byte-identity is enforced on every PR by the
`P-cross-runtime-emit-parity` property test at 50 runs plus seven
committed fixtures. Hashing parity (`P-hashing-parity`) runs at 100
runs × 2 functions.

Any drift between Python and TS output is treated as a release blocker.

## Deprecation process

When a symbol is deprecated:

1. The next minor release ships the symbol still functional, but with
   a JSDoc `@deprecated` marker visible to TypeScript and a runtime
   warning emitted once per process on first use.
2. The subsequent major release may remove the symbol.
3. The CHANGELOG entry and the release notes both call the deprecation
   out in the `[SDK]` section.
