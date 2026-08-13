/**
 * AC-938: `AUTOCONTEXT_OFFLINE` is not enforced by this engine, so it refuses.
 *
 * AC-917 landed offline mode in Python, where one rule holds:
 *
 *     Offline mode means the engine never initiates an outbound connection.
 *
 * It is enforced there by guards at each egress boundary, a test that runs a
 * complete generation with a guard on `socket.socket.connect` and asserts zero
 * connection attempts, and a CI guard that fails on new unguarded call sites.
 *
 * **None of that exists here yet.** Roughly six modules in `ts/src` make
 * engine-initiated outbound calls (`providers/provider-factory`,
 * `notifications`, `traces/publishing-workflow`,
 * `control-plane/agent-app-node`, `config/oauth`, `cli/commands/auth`) and none
 * of them is guarded.
 *
 * So this refuses rather than pretending. **An unenforced guarantee that looks
 * enforced is worse than an absent one:** an operator who sets the flag and
 * sees a run start reasonably concludes the guarantee holds, and finds out
 * otherwise from a packet capture rather than from us.
 *
 * Deliberately NOT a partial implementation. Adding `requireOnline` to those
 * six sites without the socket-level proof would make offline mode *look* like
 * it works here, which is the exact failure this file exists to prevent, just
 * with more code and more confidence behind it. The port lands with its proof
 * or not at all.
 *
 * When it does land, this module is what it replaces.
 */

const TRUE_VALUES = new Set(["1", "true", "yes", "on"]);

/** Message shown when the flag is set. Names the engine, not just the failure. */
export const OFFLINE_UNSUPPORTED_MESSAGE =
  "AUTOCONTEXT_OFFLINE is set, but the TypeScript engine does not enforce offline mode. " +
  "It is enforced by the Python engine (AUTOCONTEXT_OFFLINE, autocontext package); " +
  "TypeScript support is tracked as AC-938. " +
  "Refusing to start rather than run unenforced: a guarantee that looks enforced but is not " +
  "is worse than one that is absent. Unset AUTOCONTEXT_OFFLINE to run on this engine.";

/** Raised at startup when offline mode is requested from an engine that cannot honor it. */
export class OfflineUnsupportedError extends Error {
  constructor(message: string = OFFLINE_UNSUPPORTED_MESSAGE) {
    super(message);
    this.name = "OfflineUnsupportedError";
  }
}

/** Whether offline mode was requested in this environment. */
export function offlineRequested(env: Record<string, string | undefined> = process.env): boolean {
  return TRUE_VALUES.has((env.AUTOCONTEXT_OFFLINE ?? "").trim().toLowerCase());
}

/**
 * Refuse to proceed when offline mode is requested.
 *
 * Called from settings assembly, which every run passes through, rather than
 * from a CLI entry point: a second entry point added later would otherwise
 * quietly bypass it.
 */
export function assertOfflineSupported(
  env: Record<string, string | undefined> = process.env,
): void {
  if (offlineRequested(env)) {
    throw new OfflineUnsupportedError();
  }
}
