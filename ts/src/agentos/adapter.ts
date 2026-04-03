/**
 * agentOS session adapter (AC-517).
 *
 * DDD: AgentOsSessionAdapter is an application service that bridges
 * autocontext's Session aggregate to agentOS's VM runtime. It:
 * - Creates autocontext Sessions backed by agentOS VM sessions
 * - Maps submitTurn → os.prompt → turn completion
 * - Propagates events from agentOS to autocontext's event stream
 * - Manages the session-to-VM mapping
 */

import { Session, SessionStatus, TurnOutcome } from "../session/types.js";
import type { AgentOsRuntimePort } from "./types.js";
import { AgentOsConfig } from "./types.js";

interface TurnResult {
  response: string;
  outcome: string;
}

interface SessionBinding {
  session: Session;
  aosSessionId: string;
  aosEvents: Array<{ method?: string; params?: Record<string, unknown> }>;
}

export class AgentOsSessionAdapter {
  private runtime: AgentOsRuntimePort;
  private config: AgentOsConfig;
  private bindings = new Map<string, SessionBinding>();

  constructor(runtime: AgentOsRuntimePort, config: AgentOsConfig) {
    this.runtime = runtime;
    this.config = config;
  }

  async startSession(goal: string): Promise<Session> {
    if (!this.config.enabled) {
      throw new Error("agentOS integration is disabled");
    }

    const session = Session.create({ goal, metadata: { runtime: "agentos", agentType: this.config.agentType } });

    const { sessionId: aosSessionId } = await this.runtime.createSession(this.config.agentType, {
      env: {},
    });

    const binding: SessionBinding = { session, aosSessionId, aosEvents: [] };
    this.bindings.set(session.sessionId, binding);

    // Wire agentOS events into session event stream
    this.runtime.onSessionEvent(aosSessionId, (event) => {
      binding.aosEvents.push(event as SessionBinding["aosEvents"][number]);
    });

    return session;
  }

  async submitTurn(sessionId: string, prompt: string): Promise<TurnResult> {
    const binding = this.getBinding(sessionId);
    const { session, aosSessionId } = binding;

    // Submit turn through autocontext's session model
    const turn = session.submitTurn({ prompt, role: "operator" });

    try {
      // Forward to agentOS runtime
      await this.runtime.prompt(aosSessionId, prompt);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      session.failTurn(turn.turnId, message);
      throw error;
    }

    // Collect response from agentOS events
    const lastMessage = [...binding.aosEvents].reverse().find((e) => e.method === "message" && e.params?.role === "assistant");
    const response = (lastMessage?.params?.content as string) ?? "";

    // Complete the turn
    session.completeTurn(turn.turnId, { response, tokensUsed: 0 });

    return { response, outcome: TurnOutcome.COMPLETED };
  }

  async closeSession(sessionId: string): Promise<void> {
    const binding = this.getBinding(sessionId);
    const { session, aosSessionId } = binding;

    await this.runtime.closeSession(aosSessionId);
    session.complete("Session closed via agentOS adapter");
  }

  get activeSessions(): Session[] {
    return [...this.bindings.values()]
      .map((b) => b.session)
      .filter((s) => s.status === SessionStatus.ACTIVE);
  }

  getSession(sessionId: string): Session | undefined {
    return this.bindings.get(sessionId)?.session;
  }

  private getBinding(sessionId: string): SessionBinding {
    const binding = this.bindings.get(sessionId);
    if (!binding) throw new Error(`Session '${sessionId}' not found in adapter`);
    if (binding.session.status !== SessionStatus.ACTIVE) {
      throw new Error(`Session '${sessionId}' is not active (status=${binding.session.status})`);
    }
    return binding;
  }
}
