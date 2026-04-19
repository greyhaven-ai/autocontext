// Output formatters for the production-traces CLI.
//
// The control-plane's `_shared/output-formatters.ts` already implements a
// `formatOutput(value, mode)` helper with identical semantics to what this
// CLI needs (json / table / pretty). To keep DRY discipline (per the Layer 7
// brief), we re-export the proven implementation here instead of cloning it.
//
// If the two CLIs ever need to diverge in output shape, replace the re-export
// with a local implementation — the consumer-facing import path stays stable.

export { formatOutput } from "../../../control-plane/cli/_shared/output-formatters.js";
export type { OutputMode } from "../../../control-plane/cli/_shared/output-formatters.js";
