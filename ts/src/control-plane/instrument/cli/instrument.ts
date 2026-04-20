/**
 * A2-I Layer 7 — `autoctx instrument` entry point.
 *
 * Thin shim that re-exports the in-process runner so `ts/src/cli/index.ts`
 * can dispatch to a single function. The runner itself owns flag parsing +
 * the `runInstrument` call; this module's only job is to name the entry
 * point the way the outer CLI expects (parallel to `emit-pr.ts`).
 */
export {
  runInstrumentCommand,
  INSTRUMENT_HELP_TEXT,
  type CliResult,
  type RunnerOpts,
} from "./runner.js";
