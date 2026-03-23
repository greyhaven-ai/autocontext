/**
 * Pi RPC runtime — HTTP RPC communication with Pi (AC-361).
 * Mirrors Python's autocontext/runtimes/pi_rpc.py.
 * Supports session persistence and per-role isolation.
 */

import type { AgentOutput } from "./base.js";

export interface PiRPCConfigOpts {
  endpoint?: string;
  apiKey?: string;
  timeout?: number;
  sessionPersistence?: boolean;
}

export class PiRPCConfig {
  readonly endpoint: string;
  readonly apiKey: string;
  readonly timeout: number;
  readonly sessionPersistence: boolean;

  constructor(opts: PiRPCConfigOpts = {}) {
    this.endpoint = opts.endpoint ?? "http://localhost:3284";
    this.apiKey = opts.apiKey ?? "";
    this.timeout = opts.timeout ?? 120.0;
    this.sessionPersistence = opts.sessionPersistence ?? true;
  }
}

export class PiRPCRuntime {
  readonly name = "pi-rpc";
  private config: PiRPCConfig;
  private _currentSessionId: string | null = null;

  constructor(config?: PiRPCConfig) {
    this.config = config ?? new PiRPCConfig();
  }

  get currentSessionId(): string | null {
    return this._currentSessionId;
  }

  async generate(opts: {
    prompt: string;
    system?: string;
  }): Promise<AgentOutput> {
    const payload: Record<string, unknown> = { prompt: opts.prompt };
    if (opts.system) payload.system = opts.system;
    if (this._currentSessionId && this.config.sessionPersistence) {
      payload.session_id = this._currentSessionId;
    }

    try {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (this.config.apiKey) headers.Authorization = `Bearer ${this.config.apiKey}`;

      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), this.config.timeout * 1000);

      const res = await fetch(`${this.config.endpoint}/v1/generate`, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      clearTimeout(timeout);

      if (!res.ok) {
        const body = await res.text();
        return { text: "", metadata: { error: `HTTP ${res.status}: ${body.slice(0, 200)}` } };
      }

      const data = (await res.json()) as Record<string, unknown>;
      if (data.session_id && typeof data.session_id === "string") {
        this._currentSessionId = data.session_id;
      }
      const text = typeof data.text === "string" ? data.text : typeof data.response === "string" ? data.response : "";
      return { text, metadata: { sessionId: this._currentSessionId } };
    } catch (err: unknown) {
      const error = err as { name?: string; message?: string };
      if (error.name === "AbortError") {
        return { text: "", metadata: { error: "timeout" } };
      }
      return { text: "", metadata: { error: error.message ?? "unknown" } };
    }
  }

  async revise(opts: {
    prompt: string;
    previousOutput: string;
    feedback: string;
  }): Promise<AgentOutput> {
    return this.generate({
      prompt: [
        `Revise the following output based on the judge's feedback.`,
        `## Original Output\n${opts.previousOutput}`,
        `## Judge Feedback\n${opts.feedback}`,
        `## Original Task\n${opts.prompt}`,
        `Produce an improved version:`,
      ].join("\n\n"),
    });
  }
}
