import { randomUUID } from "node:crypto";
import { ChromeCdpSession, type ChromeCdpTransport } from "./chrome-cdp.js";
import {
  ChromeCdpTargetDiscovery,
  type ChromeCdpTargetDiscoveryPort,
} from "./chrome-cdp-discovery.js";
import { ChromeCdpWebSocketTransport } from "./chrome-cdp-transport.js";
import { BrowserEvidenceStore } from "./evidence.js";
import type { BrowserRuntimePort, BrowserSessionConfig, BrowserSessionPort } from "./types.js";

export type ChromeCdpTransportFactory = (url: string) => ChromeCdpTransport;
export type BrowserSessionIdFactory = () => string;

export interface ChromeCdpRuntimeOpts {
  readonly websocketUrl?: string;
  readonly debuggerUrl?: string;
  readonly preferredTargetUrl?: string;
  readonly evidenceRoot?: string;
  readonly targetDiscovery?: ChromeCdpTargetDiscoveryPort;
  readonly transportFactory?: ChromeCdpTransportFactory;
  readonly sessionIdFactory?: BrowserSessionIdFactory;
}

export class ChromeCdpRuntime implements BrowserRuntimePort {
  readonly websocketUrl: string | null;
  readonly debuggerUrl: string | null;
  readonly evidenceRoot: string | null;
  readonly preferredTargetUrl: string | null;

  private readonly transportFactory: ChromeCdpTransportFactory;
  private readonly sessionIdFactory: BrowserSessionIdFactory;
  private readonly targetDiscovery: ChromeCdpTargetDiscoveryPort | null;

  constructor(opts: ChromeCdpRuntimeOpts) {
    if (!opts.websocketUrl && !opts.debuggerUrl && !opts.targetDiscovery) {
      throw new Error("ChromeCdpRuntime requires websocketUrl, debuggerUrl, or targetDiscovery");
    }
    this.websocketUrl = opts.websocketUrl ?? null;
    this.debuggerUrl = opts.debuggerUrl ?? null;
    this.evidenceRoot = opts.evidenceRoot ?? null;
    this.preferredTargetUrl = opts.preferredTargetUrl ?? null;
    this.targetDiscovery = opts.targetDiscovery ?? null;
    this.transportFactory = opts.transportFactory ?? ((url) => new ChromeCdpWebSocketTransport({ url }));
    this.sessionIdFactory = opts.sessionIdFactory ?? defaultSessionId;
  }

  async createSession(config: BrowserSessionConfig): Promise<BrowserSessionPort> {
    const websocketUrl = await this.resolveWebSocketUrl(config);
    return new ChromeCdpSession({
      sessionId: this.sessionIdFactory(),
      config,
      transport: this.transportFactory(websocketUrl),
      evidenceStore: this.evidenceRoot ? new BrowserEvidenceStore({ rootDir: this.evidenceRoot }) : undefined,
    });
  }

  private async resolveWebSocketUrl(config: BrowserSessionConfig): Promise<string> {
    if (this.websocketUrl) {
      return this.websocketUrl;
    }
    const discovery =
      this.targetDiscovery ??
      (this.debuggerUrl ? new ChromeCdpTargetDiscovery({ debuggerUrl: this.debuggerUrl }) : null);
    if (!discovery) {
      throw new Error("ChromeCdpRuntime cannot resolve a websocket target without discovery");
    }
    return await discovery.resolveWebSocketUrl(config, {
      preferredUrl: this.preferredTargetUrl ?? undefined,
    });
  }
}

function defaultSessionId(): string {
  return `browser_${randomUUID().replaceAll("-", "")}`;
}
