/**
 * A2-I Layer 7 — CLI barrel.
 *
 * Only the runner + help text are public. Internals (flag parser, output
 * formatting) are not re-exported to keep the blast radius small.
 */
export { runInstrumentCommand, INSTRUMENT_HELP_TEXT } from "./instrument.js";
export type { CliResult, RunnerOpts } from "./instrument.js";
