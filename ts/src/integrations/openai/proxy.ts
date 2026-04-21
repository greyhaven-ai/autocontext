/**
 * ClientProxy — Proxy-based wrapper around an OpenAI client.
 *
 * Intercepts .chat.completions.create / .responses.create. All other
 * attribute access passes through transparently. Mirror of Python ``_proxy.py``.
 */

// proxy.ts will be implemented in Task 3.7
export class ClientProxy {
  constructor(_opts: unknown) {
    throw new Error("stub");
  }
}
