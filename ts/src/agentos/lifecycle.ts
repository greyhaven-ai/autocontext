/**
 * agentOS VM lifecycle management (AC-517).
 *
 * DDD: AgentOsLifecycle manages the VM-level concerns:
 * - Workspace mounting
 * - Startup/shutdown
 * - Sandbox escalation detection
 */

import type { AgentOsRuntimePort } from "./types.js";

const SANDBOX_KEYWORDS = [
  "browser", "playwright", "puppeteer", "selenium",
  "dev server", "port 3000", "port 8080", "localhost",
  "gui", "native build", "docker", "container",
];

export class AgentOsLifecycle {
  private runtime: AgentOsRuntimePort;
  private _mountedPaths: string[] = [];
  private _activeSessions = new Map<string, string>(); // autocontext sessionId → agentOS sessionId
  private _isShutdown = false;

  constructor(runtime: AgentOsRuntimePort) {
    this.runtime = runtime;
  }

  get mountedPaths(): string[] { return [...this._mountedPaths]; }
  get isShutdown(): boolean { return this._isShutdown; }

  async mountWorkspace(hostPath: string): Promise<void> {
    // agentOS host-dir mounts are configured at creation time,
    // but we track them here for lifecycle visibility
    this._mountedPaths.push(hostPath);
  }

  async startSession(sessionId: string, agentType: string): Promise<string> {
    const { sessionId: aosSessionId } = await this.runtime.createSession(agentType);
    this._activeSessions.set(sessionId, aosSessionId);
    return aosSessionId;
  }

  async closeSession(sessionId: string): Promise<void> {
    const aosSessionId = this._activeSessions.get(sessionId);
    if (aosSessionId) {
      await this.runtime.closeSession(aosSessionId);
      this._activeSessions.delete(sessionId);
    }
  }

  async shutdown(): Promise<void> {
    // Close all active sessions
    for (const [sid] of this._activeSessions) {
      await this.closeSession(sid);
    }
    await this.runtime.dispose();
    this._isShutdown = true;
  }

  /**
   * Heuristic: does this task description suggest a full sandbox is needed?
   *
   * agentOS handles coding, scripts, filesystem work. But browser automation,
   * dev servers, GUI apps, and native builds need a full sandbox.
   */
  needsSandbox(taskDescription: string): boolean {
    const lower = taskDescription.toLowerCase();
    return SANDBOX_KEYWORDS.some((kw) => lower.includes(kw));
  }
}
