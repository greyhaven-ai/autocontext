// Output formatters for the production-traces CLI.
//
// Both CLIs use the neutral `cli/shared-output-formatters.ts` implementation,
// so neither domain owns a reverse dependency on the other.
//
// If the two CLIs ever need to diverge in output shape, replace the re-export
// with a local implementation — the consumer-facing import path stays stable.

export { formatOutput } from "../../../cli/shared-output-formatters.js";
export type { OutputMode } from "../../../cli/shared-output-formatters.js";
