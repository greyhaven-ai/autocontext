import type { BrowserSessionConfig } from "./contract/index.js";
import { evaluateBrowserActionPolicy } from "./policy.js";

export class ChromeCdpDiscoveryError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ChromeCdpDiscoveryError";
  }
}

export interface ChromeCdpTarget {
  readonly targetId: string;
  readonly targetType: string;
  readonly title: string;
  readonly url: string;
  readonly webSocketDebuggerUrl: string;
}

export interface ChromeCdpTargetDiscoveryPort {
  resolveWebSocketUrl(
    config: BrowserSessionConfig,
    opts?: { preferredUrl?: string },
  ): Promise<string>;
}

export interface BrowserFetchResponseLike {
  readonly ok: boolean;
  readonly status: number;
  json(): Promise<unknown>;
}

export type BrowserFetchFn = (input: string, init?: RequestInit) => Promise<BrowserFetchResponseLike>;

export interface ChromeCdpTargetDiscoveryOpts {
  readonly debuggerUrl: string;
  readonly fetchFn?: BrowserFetchFn;
}

export class ChromeCdpTargetDiscovery implements ChromeCdpTargetDiscoveryPort {
  readonly debuggerUrl: string;

  private readonly fetchFn: BrowserFetchFn;

  constructor(opts: ChromeCdpTargetDiscoveryOpts) {
    this.debuggerUrl = opts.debuggerUrl.replace(/\/+$/, "");
    this.fetchFn = opts.fetchFn ?? defaultFetch;
  }

  async listTargets(): Promise<ChromeCdpTarget[]> {
    const response = await this.fetchFn(`${this.debuggerUrl}/json/list`);
    if (!response.ok) {
      throw new ChromeCdpDiscoveryError(
        `Debugger target discovery failed with HTTP ${response.status}`,
      );
    }
    const payload = await response.json();
    if (!Array.isArray(payload)) {
      throw new ChromeCdpDiscoveryError("Debugger target discovery expected a JSON array from /json/list");
    }
    return payload.flatMap((entry) => {
      const target = parseTarget(entry);
      return target ? [target] : [];
    });
  }

  async resolveWebSocketUrl(
    config: BrowserSessionConfig,
    opts: { preferredUrl?: string } = {},
  ): Promise<string> {
    const target = selectChromeCdpTarget(await this.listTargets(), config, opts);
    return target.webSocketDebuggerUrl;
  }
}

export function selectChromeCdpTarget(
  targets: readonly ChromeCdpTarget[],
  config: BrowserSessionConfig,
  opts: { preferredUrl?: string } = {},
): ChromeCdpTarget {
  const attachableTargets = targets.filter(
    (target) => target.targetType === "page" && target.webSocketDebuggerUrl.length > 0,
  );
  if (opts.preferredUrl) {
    const preferredTarget = attachableTargets.find((target) => target.url === opts.preferredUrl);
    if (preferredTarget) {
      if (isTargetAllowed(config, preferredTarget.url)) {
        return preferredTarget;
      }
      throw new ChromeCdpDiscoveryError(
        `Preferred debugger target is not allowed by browser policy: ${opts.preferredUrl}`,
      );
    }
  }

  const allowedTargets = attachableTargets.filter((target) => isTargetAllowed(config, target.url));
  if (allowedTargets.length > 0) {
    return allowedTargets[0];
  }
  if (attachableTargets.length === 0) {
    throw new ChromeCdpDiscoveryError("No attachable page targets were advertised by the debugger");
  }
  if (opts.preferredUrl) {
    throw new ChromeCdpDiscoveryError(`Preferred debugger target was not found: ${opts.preferredUrl}`);
  }
  throw new ChromeCdpDiscoveryError("No debugger targets matched the browser allowlist");
}

function parseTarget(payload: unknown): ChromeCdpTarget | null {
  if (!isRecord(payload) || typeof payload.id !== "string" || typeof payload.type !== "string") {
    return null;
  }
  return {
    targetId: payload.id,
    targetType: payload.type,
    title: typeof payload.title === "string" ? payload.title : "",
    url: typeof payload.url === "string" ? payload.url : "",
    webSocketDebuggerUrl:
      typeof payload.webSocketDebuggerUrl === "string" ? payload.webSocketDebuggerUrl : "",
  };
}

function isTargetAllowed(config: BrowserSessionConfig, url: string): boolean {
  return evaluateBrowserActionPolicy(config, {
    schemaVersion: "1.0",
    actionId: "act_discovery_probe",
    sessionId: "session_discovery",
    timestamp: new Date().toISOString(),
    type: "navigate",
    params: { url },
  }).allowed;
}

async function defaultFetch(input: string, init?: RequestInit): Promise<BrowserFetchResponseLike> {
  const response = await fetch(input, init);
  return response;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
