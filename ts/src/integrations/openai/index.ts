/**
 * ``autoctx/integrations/openai`` — customer-facing OpenAI instrumentation runtime.
 *
 * Public surface: ``instrumentClient``, ``FileSink``, ``autocontextSession``,
 * ``TraceSink``. See ``STABILITY.md`` for stability commitments.
 *
 * DDD anchor: mirrors ``autocontext.integrations.openai`` Python package —
 * same public symbols, same wire behavior, byte-identical traces (enforced by
 * cross-runtime parity tests). Spec §4.1 + §6.2 + §7.2.
 *
 * Zero telemetry. Traces go where you put them.
 */

export { autocontextSession, currentSession } from "./session.js";
export type { SessionContext } from "./session.js";

export { FileSink } from "./sink.js";
export type { TraceSink } from "./sink.js";

export { instrumentClient } from "./wrap.js";
