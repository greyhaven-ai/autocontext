// Public surface for `autocontext/production-traces`.
// Layer 1 exposes `contract/`; Layer 3 adds `ingest/`; Layer 4 adds `redaction/`;
// Layer 5 adds `dataset/`. Later layers will add retention/, cli/.
export * as contract from "./contract/index.js";
export * as ingest from "./ingest/index.js";
export * as redaction from "./redaction/index.js";
export * as dataset from "./dataset/index.js";
