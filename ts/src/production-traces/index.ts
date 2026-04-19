// Public surface for `autocontext/production-traces`.
// Layer 1 exposes `contract/`; Layer 3 adds `ingest/`; Layer 4 adds `redaction/`;
// Layer 5 adds `dataset/`; Layer 7 adds `cli/`. Later layers will add retention/
// and expand the CLI-to-module surface.
export * as contract from "./contract/index.js";
export * as ingest from "./ingest/index.js";
export * as redaction from "./redaction/index.js";
export * as dataset from "./dataset/index.js";
export * as cli from "./cli/index.js";
