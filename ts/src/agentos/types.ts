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

export const AGENT_OS_FILESYSTEM_MODES = ["none", "readonly", "readwrite"] as const;
export type AgentOsFilesystemMode = (typeof AGENT_OS_FILESYSTEM_MODES)[number];

const AGENT_OS_PERMISSIONS_DEFAULTS = {
  network: false,
  filesystem: "readonly" as AgentOsFilesystemMode,
  processes: false,
  maxMemoryMb: 512,
};

export type AgentOsPermissionsOpts = Partial<typeof AGENT_OS_PERMISSIONS_DEFAULTS>;

const DEFAULT_SANDBOX_ESCALATION_KEYWORDS = [
  "browser", "playwright", "puppeteer", "selenium",
  "dev server", "port", "localhost",
  "gui", "native build", "docker",
] as const;

export class AgentOsPermissions {
  readonly network!: boolean;
  readonly filesystem!: AgentOsFilesystemMode;
  readonly processes!: boolean;
  readonly maxMemoryMb!: number;

  constructor(opts: AgentOsPermissionsOpts = {}) {
    const resolved = { ...AGENT_OS_PERMISSIONS_DEFAULTS, ...opts };
    Object.assign(this, resolved);
  }
}

const AGENT_OS_CONFIG_DEFAULTS = {
  enabled: false,
  agentType: "pi",
  workspacePath: "",
};

export type AgentOsConfigOpts = Partial<typeof AGENT_OS_CONFIG_DEFAULTS> & {
  permissions?: AgentOsPermissions;
  sandboxEscalationKeywords?: string[];
};

export class AgentOsConfig {
  readonly enabled!: boolean;
  readonly agentType!: string;
  readonly workspacePath!: string;
  readonly permissions: AgentOsPermissions;
  readonly sandboxEscalationKeywords: string[];

  constructor(opts: AgentOsConfigOpts = {}) {
    const resolved = { ...AGENT_OS_CONFIG_DEFAULTS, ...opts };
    Object.assign(this, {
      enabled: resolved.enabled,
      agentType: resolved.agentType,
      workspacePath: resolved.workspacePath,
    });
    this.permissions = opts.permissions ?? new AgentOsPermissions();
    this.sandboxEscalationKeywords = [
      ...(opts.sandboxEscalationKeywords ?? DEFAULT_SANDBOX_ESCALATION_KEYWORDS),
    ];
  }
}
