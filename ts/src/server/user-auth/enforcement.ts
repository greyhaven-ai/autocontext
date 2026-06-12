/**
 * Commands allowed before/without authentication: the auth handshake itself,
 * read-only listing, and the provider-auth (LLM/API-key) setup commands, which
 * are a separate concern from user identity.
 */
const AUTH_FREE_COMMANDS = new Set<string>([
  "authenticate",
  "list_scenarios",
  "whoami",
  "login",
  "logout",
  "switch_provider",
]);

/** Whether a ws command requires a verified identity (when auth is enabled). */
export function commandRequiresAuth(type: string): boolean {
  return !AUTH_FREE_COMMANDS.has(type);
}

/** Whether an HTTP method is mutating and requires a verified identity. */
export function httpRequiresAuth(method: string): boolean {
  const m = method.toUpperCase();
  return m !== "GET" && m !== "HEAD" && m !== "OPTIONS";
}
