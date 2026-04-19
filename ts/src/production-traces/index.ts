// Public surface for `autocontext/production-traces`.
// Layer 1 exposes `contract/`; Layer 3 adds `ingest/`.
// Later layers will add redaction/, dataset/, retention/, cli/.
export * as contract from "./contract/index.js";
export * as ingest from "./ingest/index.js";
