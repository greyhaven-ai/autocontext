/**
 * autocontextSession AsyncLocalStorage + currentSession.
 *
 * Spec §4.1. Uses AsyncLocalStorage; propagates naturally across ``await``,
 * across ``setTimeout``, across ``Promise.all`` — mirrors Python contextvar
 * behavior. NOT propagated across raw ``new Worker()`` threads.
 * Mirror of Python ``autocontext.integrations.openai._session``.
 */
import { AsyncLocalStorage } from "node:async_hooks";

export type SessionContext = {
  userId?: string;
  sessionId?: string;
};

const _store = new AsyncLocalStorage<SessionContext>();

/**
 * Run ``fn`` with ``ctx`` as the active session context.
 * Mirrors Python's ``autocontext_session`` context manager.
 */
export async function autocontextSession(
  ctx: SessionContext,
  fn: () => void | Promise<void>,
): Promise<void> {
  await _store.run(ctx, fn);
}

/**
 * Read the active session. Returns ``{}`` when no session is active.
 * Mirrors Python's ``current_session()``.
 */
export function currentSession(): SessionContext {
  return _store.getStore() ?? {};
}
