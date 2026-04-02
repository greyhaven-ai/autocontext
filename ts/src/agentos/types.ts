/**
 * agentOS integration types (AC-517).
 *
 * DDD: Port types that define the boundary between autocontext's
 * session domain and agentOS's VM runtime. The runtime port is a
 * protocol — no direct dependency on @rivet-dev/agent-os-core.
 */

/**
 * Port interface for agentOS runtime.
 *
 * This is the ONLY surface autocontext depends on. Implementors
 * can use real AgentOs or a stub for testing.
 */
export interface AgentOsRuntimePort {
  createSession(agentType: string, opts?: Record<string, unknown>): Promise<{ sessionId: string }>;
  prompt(sessionId: string, prompt: string): Promise<void>;
  onSessionEvent(sessionId: string, handler: (event: unknown) => void): void;
  closeSession(sessionId: string): Promise<void>;
  writeFile(path: string, content: string | Uint8Array): Promise<void>;
  readFile(path: string): Promise<Uint8Array>;
  dispose(): Promise<void>;
}

export class AgentOsPermissions {
  readonly network: boolean;
  readonly filesystem: "none" | "readonly" | "readwrite";
  readonly processes: boolean;
  readonly maxMemoryMb: number;

  constructor(opts?: {
    network?: boolean;
    filesystem?: "none" | "readonly" | "readwrite";
    processes?: boolean;
    maxMemoryMb?: number;
  }) {
    this.network = opts?.network ?? false;
    this.filesystem = opts?.filesystem ?? "readonly";
    this.processes = opts?.processes ?? false;
    this.maxMemoryMb = opts?.maxMemoryMb ?? 512;
  }
}

export class AgentOsConfig {
  readonly enabled: boolean;
  readonly agentType: string;
  readonly workspacePath: string;
  readonly permissions: AgentOsPermissions;
  readonly sandboxEscalationKeywords: string[];

  constructor(opts?: {
    enabled?: boolean;
    agentType?: string;
    workspacePath?: string;
    permissions?: AgentOsPermissions;
    sandboxEscalationKeywords?: string[];
  }) {
    this.enabled = opts?.enabled ?? false;
    this.agentType = opts?.agentType ?? "pi";
    this.workspacePath = opts?.workspacePath ?? "";
    this.permissions = opts?.permissions ?? new AgentOsPermissions();
    this.sandboxEscalationKeywords = opts?.sandboxEscalationKeywords ?? [
      "browser", "playwright", "puppeteer", "selenium",
      "dev server", "port", "localhost",
      "GUI", "native build", "docker",
    ];
  }
}
