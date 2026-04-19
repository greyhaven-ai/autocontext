// Public surface for `autocontext/production-traces`.
// Layer 1 only exposes the contract sub-context; later layers will add
// ingest/, redaction/, dataset/, retention/, cli/.
export * as contract from "./contract/index.js";
