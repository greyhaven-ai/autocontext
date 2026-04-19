// Public surface for `autocontext/production-traces`.
// Layer 1 exposes `contract/`; Layer 3 adds `ingest/`; Layer 4 adds `redaction/`.
// Later layers will add dataset/, retention/, cli/.
export * as contract from "./contract/index.js";
export * as ingest from "./ingest/index.js";
export * as redaction from "./redaction/index.js";
