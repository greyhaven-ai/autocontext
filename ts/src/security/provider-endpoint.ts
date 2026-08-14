/**
 * Provider credentials and prompts must not leave loopback over plaintext.
 * Keep this check at the server/auth boundary; TUI callers also invoke it
 * before sending credentials as defense in depth.
 */
export function assertProviderBaseUrlIsSafe(baseUrl?: string): void {
  if (!baseUrl) return;
  let url: URL;
  try {
    url = new URL(baseUrl);
  } catch {
    throw new Error("provider base URL must be a valid http(s) URL");
  }
  if (url.username || url.password) {
    throw new Error("provider base URL must not contain embedded credentials");
  }
  if (url.protocol === "https:") return;
  const hostname = url.hostname.toLowerCase().replace(/^\[|\]$/g, "");
  const loopback = hostname === "localhost" || hostname.endsWith(".localhost") ||
    hostname === "::1" || isIpv4Loopback(hostname);
  if (url.protocol === "http:" && loopback) return;
  if (url.protocol === "http:") {
    throw new Error("remote provider base URLs must use https");
  }
  throw new Error("provider base URL must use http or https");
}

function isIpv4Loopback(hostname: string): boolean {
  const octets = hostname.split(".");
  return octets.length === 4 && octets[0] === "127" && octets.every((octet) => {
    if (!/^\d{1,3}$/.test(octet)) return false;
    const value = Number(octet);
    return value >= 0 && value <= 255;
  });
}
