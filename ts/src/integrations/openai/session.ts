/**
 * autocontextSession AsyncLocalStorage + currentSession.
 *
 * Spec §4.1. Uses AsyncLocalStorage; propagates naturally across ``await``,
 * across ``setTimeout``, across ``Promise.all`` — mirrors Python contextvar
 * behavior. NOT propagated across raw ``new Worker()`` threads.
 * Mirror of Python ``autocontext.integrations.openai._session``.
 */

// session.ts will be implemented in Task 3.3
export type SessionContext = { userId?: string; sessionId?: string };

export function autocontextSession(
  _ctx: SessionContext,
  _fn: () => void | Promise<void>,
): Promise<void> {
  throw new Error("stub");
}

export function currentSession(): SessionContext {
  return {};
}
