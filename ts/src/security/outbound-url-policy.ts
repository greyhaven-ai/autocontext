import { lookup } from "node:dns/promises";
import { isIP, type LookupFunction } from "node:net";

import {
  Agent,
  fetch as undiciFetch,
  type BodyInit as UndiciBodyInit,
  type RequestInit as UndiciRequestInit,
  type Response as UndiciResponse,
} from "undici";

export interface OutboundResolvedAddress {
  readonly address: string;
  readonly family?: number | string;
}

export type OutboundHostResolver = (
  hostname: string,
) => Promise<readonly OutboundResolvedAddress[]>;

export interface OutboundFetch {
  (url: string | URL, init?: RequestInit): Promise<Response>;
  close?(): Promise<void>;
}

export interface SafeOutboundUrlOptions {
  readonly resolveHostname?: OutboundHostResolver;
  readonly resolveTimeoutMs?: number;
}

export interface SafeOutboundFetchOptions extends SafeOutboundUrlOptions {
  readonly fetch?: OutboundFetch;
  readonly maxRedirects?: number;
  readonly requestTimeoutMs?: number;
  readonly maxResponseBytes?: number;
  readonly allowedResponseContentTypes?: readonly string[];
}

const REDIRECT_STATUSES = new Set([301, 302, 303, 307, 308]);
const DEFAULT_RESOLVE_TIMEOUT_MS = 5_000;
const DEFAULT_REQUEST_TIMEOUT_MS = 60_000;
const MAX_OUTBOUND_RESPONSE_BYTES = 16 * 1024 * 1024;
const ENTITY_HEADERS = ["content-encoding", "content-language", "content-length", "content-location", "content-type"];
const BLOCKED_HOSTNAMES = new Set([
  "instance-data",
  "instance-data.ec2.internal",
  "metadata",
  "metadata.aws.internal",
  "metadata.azure.internal",
  "localhost",
  "metadata.google.internal",
  "metadata.goog",
]);
const BLOCKED_METADATA_ADDRESSES = new Set([
  "100.100.100.200",
  "147.75.207.207",
  "168.63.129.16",
  "169.254.0.23",
  "169.254.169.253",
  "169.254.169.254",
  "169.254.170.2",
  "169.254.170.23",
  "192.0.0.192",
  "fd00:ec2::254",
]);

/**
 * Parse an outbound URL and reject targets that are unsafe without performing
 * DNS. The returned URL is always a fresh object so callers cannot mutate a
 * previously checked URL through a shared reference.
 */
export function assertPublicHttpUrl(value: string | URL): URL {
  let url: URL;
  try {
    url = new URL(value.toString());
  } catch {
    throw new Error("outbound URL must be a valid http(s) URL");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("outbound URL must use http: or https:");
  }
  if (!url.hostname) {
    throw new Error("outbound URL must include a hostname");
  }
  if (url.username || url.password) {
    throw new Error("outbound URL must not contain embedded credentials");
  }
  if (url.hash) {
    throw new Error("outbound URL must not contain a fragment");
  }

  const hostname = normalizeHostname(url.hostname);
  if (isBlockedHostname(hostname)) {
    throw new Error(`outbound URL hostname is not public: ${hostname}`);
  }
  if (isIP(hostname) !== 0 && !isGlobalUnicastIp(hostname)) {
    throw new Error(`outbound URL address is not public: ${hostname}`);
  }
  return url;
}

/** Resolve a target and require every returned address to be public. */
export async function assertSafeOutboundUrl(
  value: string | URL,
  options: SafeOutboundUrlOptions = {},
): Promise<URL> {
  return (await resolveSafeOutboundTarget(value, options)).url;
}

interface SafeOutboundTarget {
  readonly url: URL;
  readonly hostname: string;
  readonly addresses: readonly PinnedLookupAddress[];
}

interface PinnedLookupAddress {
  readonly address: string;
  readonly family: 4 | 6;
}

async function resolveSafeOutboundTarget(
  value: string | URL,
  options: SafeOutboundUrlOptions,
  signal?: AbortSignal,
): Promise<SafeOutboundTarget> {
  const url = assertPublicHttpUrl(value);
  const hostname = normalizeHostname(url.hostname);
  const literalFamily = ipFamily(hostname);
  if (literalFamily !== undefined) {
    return {
      url,
      hostname,
      addresses: [{ address: hostname, family: literalFamily }],
    };
  }

  const resolver = options.resolveHostname ?? defaultResolveHostname;
  const resolveTimeoutMs = options.resolveTimeoutMs ?? DEFAULT_RESOLVE_TIMEOUT_MS;
  assertPositiveBoundedInteger(resolveTimeoutMs, "resolveTimeoutMs", 60_000);
  let addresses: readonly OutboundResolvedAddress[];
  try {
    addresses = await withTimeout(
      resolver(hostname),
      resolveTimeoutMs,
      `outbound URL hostname resolution timed out: ${hostname}`,
      signal,
    );
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    throw new Error(`outbound URL hostname could not be resolved: ${hostname} (${detail})`);
  }
  if (addresses.length === 0) {
    throw new Error(`outbound URL hostname did not resolve: ${hostname}`);
  }
  const pinnedAddresses: PinnedLookupAddress[] = [];
  for (const resolved of addresses) {
    const address = normalizeHostname(resolved.address);
    const family = ipFamily(address);
    if (family === undefined || !isGlobalUnicastIp(address)) {
      throw new Error(`outbound URL hostname resolved to a non-public address: ${hostname}`);
    }
    pinnedAddresses.push({ address, family });
  }
  return { url, hostname, addresses: pinnedAddresses };
}

/**
 * Wrap fetch with public-network URL checks and manual redirect validation.
 * The destination is resolved before every request and every redirect hop.
 */
export function createSafeOutboundFetch(
  options: SafeOutboundFetchOptions = {},
): OutboundFetch {
  const requestTimeoutMs = options.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
  const maxResponseBytes = options.maxResponseBytes ?? MAX_OUTBOUND_RESPONSE_BYTES;
  assertPositiveBoundedInteger(requestTimeoutMs, "requestTimeoutMs", 10 * 60_000);
  assertPositiveBoundedInteger(maxResponseBytes, "maxResponseBytes", 128 * 1024 * 1024);
  const allowedResponseContentTypes = normalizeAllowedContentTypes(
    options.allowedResponseContentTypes,
  );
  const maxRedirects = options.maxRedirects ?? 5;
  if (!Number.isInteger(maxRedirects) || maxRedirects < 0 || maxRedirects > 20) {
    throw new Error("maxRedirects must be an integer between 0 and 20");
  }
  const pinnedAddresses = new Map<string, readonly PinnedLookupAddress[]>();
  const dispatcher = new Agent({
    connect: { lookup: createPinnedLookup((hostname) => pinnedAddresses.get(hostname)) },
    connectTimeout: 10_000,
    headersTimeout: 30_000,
    bodyTimeout: 30_000,
    maxHeaderSize: 64 * 1024,
    maxResponseSize: maxResponseBytes,
    connections: 4,
    pipelining: 1,
  });
  const fetchImpl: OutboundFetch = options.fetch ?? undiciOutboundFetch;

  const safeFetch: OutboundFetch = async (input, init = {}) => {
    const redirectMode = init.redirect ?? "follow";
    let currentUrl = assertPublicHttpUrl(input);
    const requestHeaders = normalizeOutboundRequestHeaders(init.headers);
    const deadline = createRequestDeadline(init.signal, requestTimeoutMs);

    try {
      let currentInit: RequestInit = {
        ...init,
        redirect: "manual",
        dispatcher,
        headers: requestHeaders,
        signal: deadline.signal,
      };
      for (let redirectCount = 0; ; redirectCount += 1) {
        throwIfAborted(deadline.signal);
        // Resolve exactly once for this request hop, validate every answer, and
        // make Undici's actual socket lookup return only those validated values.
        // Keeping the original URL preserves the HTTP Host header and TLS SNI.
        const target = await resolveSafeOutboundTarget(
          currentUrl,
          options,
          deadline.signal,
        );
        currentUrl = target.url;
        pinnedAddresses.set(target.hostname, target.addresses);
        const response = await withAbortSignal(
          fetchImpl(currentUrl, currentInit),
          deadline.signal,
        );
        if (!REDIRECT_STATUSES.has(response.status)) {
          return await prepareBoundedResponse(
            response,
            maxResponseBytes,
            allowedResponseContentTypes,
            deadline.cancel,
            deadline.signal,
          );
        }
        if (redirectMode === "manual") {
          return await prepareBoundedResponse(
            response,
            maxResponseBytes,
            undefined,
            deadline.cancel,
            deadline.signal,
          );
        }

        const location = response.headers.get("location");
        if (!location) {
          return await prepareBoundedResponse(
            response,
            maxResponseBytes,
            undefined,
            deadline.cancel,
            deadline.signal,
          );
        }
        if (redirectMode === "error") {
          await cancelBodyQuietly(response, deadline.signal);
          throwIfAborted(deadline.signal);
          throw new Error("outbound request redirect was not allowed");
        }
        if (redirectCount >= maxRedirects) {
          await cancelBodyQuietly(response, deadline.signal);
          throwIfAborted(deadline.signal);
          throw new Error(`outbound request exceeded ${maxRedirects} redirects`);
        }

        let nextUrl: URL;
        try {
          nextUrl = new URL(location, currentUrl);
        } catch {
          await cancelBodyQuietly(response, deadline.signal);
          throwIfAborted(deadline.signal);
          throw new Error("outbound request returned an invalid redirect URL");
        }
        try {
          nextUrl = assertPublicHttpUrl(nextUrl);
        } catch (error) {
          await cancelBodyQuietly(response, deadline.signal);
          throwIfAborted(deadline.signal);
          throw error;
        }
        const crossOrigin = nextUrl.origin !== currentUrl.origin;
        if (crossOrigin) {
          await cancelBodyQuietly(response, deadline.signal);
          throwIfAborted(deadline.signal);
          throw new Error("outbound request cross-origin redirect was not allowed");
        }
        currentInit = redirectInit(currentInit, response.status);
        currentUrl = nextUrl;
        await cancelBodyQuietly(response, deadline.signal);
        throwIfAborted(deadline.signal);
      }
    } catch (error) {
      deadline.cancel();
      throw error;
    }
  };
  safeFetch.close = async () => {
    await dispatcher.close();
  };
  return safeFetch;
}

/**
 * Build a Node lookup callback that can only return addresses already approved
 * by the URL policy. It deliberately never performs a second DNS resolution.
 */
export function createPinnedLookup(
  getPinnedAddresses: (hostname: string) => readonly OutboundResolvedAddress[] | undefined,
): LookupFunction {
  return (hostname, lookupOptions, callback) => {
    const normalizedHostname = normalizeHostname(hostname);
    const requestedFamily = lookupOptions.family === 4 || lookupOptions.family === 6
      ? lookupOptions.family
      : undefined;
    const approved = (getPinnedAddresses(normalizedHostname) ?? [])
      .map((entry) => {
        const address = normalizeHostname(entry.address);
        const family = isIP(address);
        return { address, family };
      })
      .filter((entry): entry is PinnedLookupAddress => (
        (entry.family === 4 || entry.family === 6)
        && isGlobalUnicastIp(entry.address)
        && (requestedFamily === undefined || requestedFamily === entry.family)
      ));

    if (approved.length === 0) {
      const error = Object.assign(
        new Error(`outbound socket has no approved address for ${normalizedHostname}`),
        { code: "ENOTFOUND" },
      );
      callback(error, "", 0);
      return;
    }
    if (lookupOptions.all) {
      callback(null, approved);
      return;
    }
    const selected = approved[0]!;
    callback(null, selected.address, selected.family);
  };
}

export function isGlobalUnicastIp(address: string): boolean {
  const normalized = normalizeHostname(address);
  const family = ipFamily(normalized);
  if (family === 4) return isGlobalIpv4(normalized);
  if (family === 6) return isGlobalIpv6(normalized);
  return false;
}

function ipFamily(address: string): 4 | 6 | undefined {
  const family = isIP(address);
  return family === 4 || family === 6 ? family : undefined;
}

async function undiciOutboundFetch(
  url: string | URL,
  init: RequestInit = {},
): Promise<Response> {
  const response = await undiciFetch(url, toUndiciRequestInit(init));
  return bridgeUndiciResponse(response);
}

function toUndiciRequestInit(init: RequestInit): UndiciRequestInit {
  return {
    body: toUndiciRequestBody(init.body),
    cache: init.cache,
    credentials: init.credentials,
    dispatcher: init.dispatcher,
    duplex: init.duplex,
    headers: toHeaderRecord(init.headers),
    integrity: init.integrity,
    keepalive: init.keepalive,
    method: init.method,
    mode: init.mode,
    redirect: init.redirect,
    referrer: init.referrer,
    referrerPolicy: init.referrerPolicy,
    signal: init.signal,
    window: init.window,
  };
}

function toHeaderRecord(
  headers: RequestInit["headers"],
): Record<string, string> | undefined {
  if (headers === undefined) return undefined;
  const record: Record<string, string> = {};
  new Headers(headers).forEach((value, key) => {
    record[key] = value;
  });
  return record;
}

function toUndiciRequestBody(
  body: RequestInit["body"],
): UndiciBodyInit | null | undefined {
  if (body === null || body === undefined || typeof body === "string") return body;
  if (body instanceof URLSearchParams || body instanceof ArrayBuffer) return body;
  if (ArrayBuffer.isView(body)) {
    return new Uint8Array(body.buffer, body.byteOffset, body.byteLength);
  }
  throw new Error("outbound request body type is not supported");
}

function bridgeUndiciResponse(response: UndiciResponse): Response {
  const headers = new Headers();
  response.headers.forEach((value, key) => headers.append(key, value));
  const bridged = new Response(bridgeUndiciBody(response.body), {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
  Object.defineProperties(bridged, {
    redirected: { configurable: true, value: response.redirected },
    type: { configurable: true, value: response.type },
    url: { configurable: true, value: response.url },
  });
  return bridged;
}

function bridgeUndiciBody(body: UndiciResponse["body"]): ReadableStream<Uint8Array> | null {
  if (body === null) return null;
  const reader = body.getReader();
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      try {
        const chunk = await reader.read();
        if (chunk.done) {
          controller.close();
          return;
        }
        if (!(chunk.value instanceof Uint8Array)) {
          await reader.cancel("outbound response returned a non-byte chunk");
          controller.error(new Error("outbound response returned a non-byte chunk"));
          return;
        }
        controller.enqueue(chunk.value);
      } catch (error) {
        controller.error(error);
      }
    },
    async cancel(reason) {
      await reader.cancel(reason);
    },
  });
}

interface RequestDeadline {
  readonly signal: AbortSignal;
  readonly cancel: () => void;
}

function createRequestDeadline(
  parentSignal: AbortSignal | null | undefined,
  timeoutMs: number,
): RequestDeadline {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let active = true;
  const abortFromParent = (): void => {
    controller.abort(parentSignal?.reason);
    cancel();
  };
  const cancel = (): void => {
    if (!active) return;
    active = false;
    if (timer !== undefined) clearTimeout(timer);
    parentSignal?.removeEventListener("abort", abortFromParent);
  };

  if (parentSignal?.aborted) {
    controller.abort(parentSignal.reason);
    active = false;
  } else {
    parentSignal?.addEventListener("abort", abortFromParent, { once: true });
    timer = setTimeout(() => {
      controller.abort(new Error(`outbound request exceeded ${timeoutMs}ms`));
      cancel();
    }, timeoutMs);
    timer.unref?.();
  }
  return { signal: controller.signal, cancel };
}

function normalizeAllowedContentTypes(
  values: readonly string[] | undefined,
): readonly string[] | undefined {
  if (values === undefined) return undefined;
  if (values.length === 0) {
    throw new Error("allowedResponseContentTypes must not be empty");
  }
  return values.map((value) => {
    const normalized = value.trim().toLowerCase();
    if (!/^[a-z0-9!#$&^_.+-]+\/(?:[a-z0-9!#$&^_.+-]+|\*\+[a-z0-9!#$&^_.+-]+)$/.test(normalized)) {
      throw new Error(`invalid allowed response content type: ${value}`);
    }
    return normalized;
  });
}

function normalizeOutboundRequestHeaders(
  values: RequestInit["headers"],
): Headers | undefined {
  if (values === undefined) return undefined;
  const headers = new Headers(values);
  if (headers.has("host")) {
    throw new Error("outbound request must not override the URL host");
  }
  return headers;
}

async function prepareBoundedResponse(
  response: Response,
  maxResponseBytes: number,
  allowedContentTypes: readonly string[] | undefined,
  onComplete: () => void,
  signal: AbortSignal,
): Promise<Response> {
  const rawLength = response.headers.get("content-length");
  if (rawLength !== null) {
    const normalizedLength = rawLength.trim();
    if (!/^\d+$/.test(normalizedLength) || Number(normalizedLength) > maxResponseBytes) {
      await cancelBodyQuietly(response, signal);
      throwIfAborted(signal);
      throw new Error(`outbound response exceeded ${maxResponseBytes} bytes`);
    }
  }

  if (allowedContentTypes !== undefined) {
    const rawContentType = response.headers.get("content-type");
    const contentType = rawContentType?.split(";", 1)[0]?.trim().toLowerCase();
    if (!contentType || !allowedContentTypes.some((allowed) => contentTypeMatches(contentType, allowed))) {
      await cancelBodyQuietly(response, signal);
      throwIfAborted(signal);
      throw new Error("outbound response content type was not allowed");
    }
  }

  if (response.body === null) {
    onComplete();
    return response;
  }

  const reader = response.body.getReader();
  let receivedBytes = 0;
  let completed = false;
  const complete = (): void => {
    if (completed) return;
    completed = true;
    onComplete();
  };
  const boundedBody = new ReadableStream<Uint8Array>({
    async pull(controller) {
      try {
        const chunk = await withAbortSignal(reader.read(), signal);
        if (chunk.done) {
          complete();
          controller.close();
          return;
        }
        receivedBytes += chunk.value.byteLength;
        if (receivedBytes > maxResponseBytes) {
          const error = new Error(`outbound response exceeded ${maxResponseBytes} bytes`);
          await withAbortSignal(reader.cancel(error), signal);
          complete();
          controller.error(error);
          return;
        }
        controller.enqueue(chunk.value);
      } catch (error) {
        complete();
        controller.error(error);
      }
    },
    async cancel(reason) {
      try {
        await withAbortSignal(reader.cancel(reason), signal);
      } finally {
        complete();
      }
    },
  });
  const boundedResponse = new Response(boundedBody, {
    status: response.status,
    statusText: response.statusText,
    headers: response.headers,
  });
  Object.defineProperties(boundedResponse, {
    redirected: { configurable: true, value: response.redirected },
    type: { configurable: true, value: response.type },
    url: { configurable: true, value: response.url },
  });
  return boundedResponse;
}

function contentTypeMatches(contentType: string, allowed: string): boolean {
  if (contentType === allowed) return true;
  const wildcardSuffix = allowed.indexOf("*+");
  return wildcardSuffix !== -1
    && contentType.startsWith(allowed.slice(0, wildcardSuffix))
    && contentType.endsWith(allowed.slice(wildcardSuffix + 1));
}

async function defaultResolveHostname(hostname: string): Promise<readonly OutboundResolvedAddress[]> {
  return lookup(hostname, { all: true, verbatim: true });
}

async function withTimeout<T>(
  promise: Promise<T>,
  timeoutMs: number,
  message: string,
  signal?: AbortSignal,
): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let abort: (() => void) | undefined;
  const timeout = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => reject(new Error(message)), timeoutMs);
    timer.unref?.();
  });
  const aborted = new Promise<never>((_resolve, reject) => {
    if (!signal) return;
    abort = () => reject(abortReason(signal));
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
  });
  try {
    return await Promise.race([promise, timeout, aborted]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
    if (abort !== undefined) signal?.removeEventListener("abort", abort);
  }
}

async function withAbortSignal<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  let abort: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => {
    abort = () => reject(abortReason(signal));
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
  });
  try {
    return await Promise.race([promise, aborted]);
  } finally {
    if (abort !== undefined) signal.removeEventListener("abort", abort);
  }
}

function throwIfAborted(signal: AbortSignal | undefined): void {
  if (signal?.aborted) throw abortReason(signal);
}

function abortReason(signal: AbortSignal): Error {
  if (signal.reason instanceof Error) return signal.reason;
  if (typeof signal.reason === "string" && signal.reason) {
    return new Error(signal.reason);
  }
  return new Error("outbound request was aborted");
}

function assertPositiveBoundedInteger(value: number, name: string, maximum: number): void {
  if (!Number.isInteger(value) || value < 1 || value > maximum) {
    throw new Error(`${name} must be an integer between 1 and ${maximum}`);
  }
}

function normalizeHostname(hostname: string): string {
  return hostname
    .trim()
    .toLowerCase()
    .replace(/^\[|\]$/g, "")
    .replace(/\.$/, "");
}

function isBlockedHostname(hostname: string): boolean {
  return BLOCKED_HOSTNAMES.has(hostname)
    || hostname.endsWith(".localhost")
    || hostname.endsWith(".metadata.google.internal")
    || hostname.endsWith(".instance-data.ec2.internal");
}

function isGlobalIpv4(address: string): boolean {
  if (BLOCKED_METADATA_ADDRESSES.has(address)) return false;
  const octets = address.split(".").map(Number);
  if (octets.length !== 4 || octets.some((octet) => !Number.isInteger(octet) || octet < 0 || octet > 255)) {
    return false;
  }
  const value = (
    ((octets[0]! << 24) >>> 0)
    + (octets[1]! << 16)
    + (octets[2]! << 8)
    + octets[3]!
  ) >>> 0;
  const blocked: ReadonlyArray<readonly [number, number]> = [
    [0x00000000, 8],
    [0x0a000000, 8],
    [0x64400000, 10],
    [0x7f000000, 8],
    [0xa9fe0000, 16],
    [0xac100000, 12],
    [0xc0000000, 24],
    [0xc0000200, 24],
    [0xc0a80000, 16],
    [0xc6120000, 15],
    [0xc6336400, 24],
    [0xcb007100, 24],
    [0xe0000000, 3],
  ];
  return !blocked.some(([network, prefix]) => ipv4InPrefix(value, network, prefix));
}

function ipv4InPrefix(value: number, network: number, prefix: number): boolean {
  const mask = prefix === 0 ? 0 : (0xffffffff << (32 - prefix)) >>> 0;
  return ((value & mask) >>> 0) === ((network & mask) >>> 0);
}

function isGlobalIpv6(address: string): boolean {
  const value = ipv6ToBigInt(address);
  if (value === undefined) return false;

  // IPv4-mapped IPv6 must inherit the embedded address policy.
  if ((value >> 32n) === 0xffffn) {
    return isGlobalIpv4(bigIntToIpv4(value & 0xffffffffn));
  }
  // IPv4/IPv6 translation prefixes can otherwise hide a private IPv4 target.
  if (inIpv6Prefix(value, 0x0064ff9b000000000000000000000000n, 96)
      || inIpv6Prefix(value, 0x0064ff9b000100000000000000000000n, 48)) {
    return isGlobalIpv4(bigIntToIpv4(value & 0xffffffffn));
  }

  // Only currently allocated global-unicast space is eligible. This excludes
  // unspecified, loopback, unique-local, link-local, multicast, documentation,
  // discard-only, and transition/special-purpose ranges by default.
  if (!inIpv6Prefix(value, 0x20000000000000000000000000000000n, 3)) return false;
  const blocked: ReadonlyArray<readonly [bigint, number]> = [
    [0x20010000000000000000000000000000n, 32], // Teredo and 2001 special-purpose space.
    [0x20010002000000000000000000000000n, 48], // Benchmarking.
    [0x20010010000000000000000000000000n, 28], // ORCHIDv1.
    [0x20010020000000000000000000000000n, 28], // ORCHIDv2.
    [0x20010db8000000000000000000000000n, 32], // Documentation.
    [0x20020000000000000000000000000000n, 16], // 6to4 transition space.
    [0x3fff0000000000000000000000000000n, 20], // Documentation.
  ];
  return !blocked.some(([network, prefix]) => inIpv6Prefix(value, network, prefix));
}

function inIpv6Prefix(value: bigint, network: bigint, prefix: number): boolean {
  if (prefix === 0) return true;
  const shift = BigInt(128 - prefix);
  return (value >> shift) === (network >> shift);
}

function ipv6ToBigInt(address: string): bigint | undefined {
  const zoneIndex = address.indexOf("%");
  if (zoneIndex !== -1) return undefined;
  const halves = address.split("::");
  if (halves.length > 2) return undefined;
  const left = parseIpv6Side(halves[0] ?? "");
  const right = parseIpv6Side(halves[1] ?? "");
  if (!left || !right) return undefined;
  const missing = 8 - left.length - right.length;
  if ((halves.length === 1 && missing !== 0) || (halves.length === 2 && missing < 1)) {
    return undefined;
  }
  const words = halves.length === 2
    ? [...left, ...Array<number>(missing).fill(0), ...right]
    : left;
  if (words.length !== 8) return undefined;
  return words.reduce((value, word) => (value << 16n) | BigInt(word), 0n);
}

function parseIpv6Side(side: string): number[] | undefined {
  if (!side) return [];
  const words: number[] = [];
  const parts = side.split(":");
  for (let index = 0; index < parts.length; index += 1) {
    const part = parts[index]!;
    if (part.includes(".")) {
      if (index !== parts.length - 1 || !isGlobalOrSpecialIpv4Syntax(part)) return undefined;
      const octets = part.split(".").map(Number);
      words.push((octets[0]! << 8) | octets[1]!, (octets[2]! << 8) | octets[3]!);
      continue;
    }
    if (!/^[0-9a-f]{1,4}$/i.test(part)) return undefined;
    words.push(Number.parseInt(part, 16));
  }
  return words;
}

function isGlobalOrSpecialIpv4Syntax(address: string): boolean {
  const octets = address.split(".");
  return octets.length === 4 && octets.every((octet) => /^\d{1,3}$/.test(octet) && Number(octet) <= 255);
}

function bigIntToIpv4(value: bigint): string {
  return [24n, 16n, 8n, 0n]
    .map((shift) => Number((value >> shift) & 0xffn))
    .join(".");
}

function redirectInit(init: RequestInit, status: number): RequestInit {
  const method = (init.method ?? "GET").toUpperCase();
  const switchToGet = status === 303 || ((status === 301 || status === 302) && method === "POST");
  const headers = new Headers(init.headers);
  if (switchToGet) {
    for (const header of ENTITY_HEADERS) headers.delete(header);
  }
  return {
    ...init,
    redirect: "manual",
    headers,
    method: switchToGet ? "GET" : method,
    body: switchToGet ? undefined : init.body,
  };
}

async function cancelBodyQuietly(
  response: Response,
  signal?: AbortSignal,
): Promise<void> {
  try {
    const cancellation = response.body?.cancel();
    if (cancellation === undefined) return;
    if (signal === undefined) {
      await cancellation;
    } else {
      await withAbortSignal(cancellation, signal);
    }
  } catch {
    // Redirect policy errors should remain the reported failure.
  }
}
