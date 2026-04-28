// Compatibility path. The pure canonical JSON implementation is core-owned by
// the production-traces contract package so SDK helpers can use it without
// importing control-plane code.
export { canonicalJsonStringify } from "../../production-traces/contract/canonical-json.js";
export type { JsonValue } from "../../production-traces/contract/canonical-json.js";
