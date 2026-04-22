/**
 * Customer-facing Anthropic integration.
 * Public surface: `instrumentClient`, `FileSink`, `TraceSink`, `autocontextSession`.
 */
export { instrumentClient } from "./wrap.js";
export { FileSink, autocontextSession, currentSession } from "../_shared/index.js";
export type { TraceSink, FileSinkOptions as FileSinkOpts } from "../_shared/index.js";
