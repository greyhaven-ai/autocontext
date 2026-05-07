/**
 * Pi RPC runtime — subprocess stdin/stdout JSONL communication with Pi.
 * Mirrors Python's autocontext/runtimes/pi_rpc.py.
 */

import { randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import type { AgentOutput } from "./base.js";
import { definedConfigOptions } from "./config-options.js";

export interface PiRPCConfigOpts {
  piCommand?: string;
  model?: string;
  timeout?: number;
  workspace?: string;
  sessionPersistence?: boolean;
  noContextFiles?: boolean;
  extraArgs?: string[];
}

const PI_RPC_CONFIG_DEFAULTS = {
  piCommand: "pi",
  model: "",
  timeout: 120.0,
  workspace: "",
  sessionPersistence: true,
  noContextFiles: false,
  extraArgs: [] as string[],
};

export class PiRPCConfig {
  readonly piCommand!: string;
  readonly model!: string;
  readonly timeout!: number;
  readonly workspace!: string;
  readonly sessionPersistence!: boolean;
  readonly noContextFiles!: boolean;
  readonly extraArgs!: string[];

  constructor(opts: PiRPCConfigOpts = {}) {
    Object.assign(this, {
      ...PI_RPC_CONFIG_DEFAULTS,
      ...definedConfigOptions(opts),
      extraArgs: [...(opts.extraArgs ?? PI_RPC_CONFIG_DEFAULTS.extraArgs)],
    });
  }
}

export class PiRPCRuntime {
  readonly name = "pi-rpc";
  protected config: PiRPCConfig;
  protected _currentSessionId: string | null = null;

  constructor(config?: PiRPCConfig) {
    this.config = config ?? new PiRPCConfig();
  }

  get currentSessionId(): string | null {
    return this._currentSessionId;
  }

  async generate(opts: { prompt: string; system?: string }): Promise<AgentOutput> {
    const fullPrompt = opts.system ? `${opts.system}\n\n${opts.prompt}` : opts.prompt;
    const args = this.buildArgs();
    const input = `${JSON.stringify(this.buildPromptCommand(fullPrompt))}\n`;

    return this.invokeRpc(args, input);
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

  protected buildArgs(): string[] {
    const args = ["--mode", "rpc"];
    if (this.config.model) {
      args.push("--model", this.config.model);
    }
    if (this.config.noContextFiles) {
      args.push("--no-context-files");
    }
    if (!this.config.sessionPersistence) {
      args.push("--no-session");
    }
    args.push(...this.config.extraArgs);
    return args;
  }

  protected buildPromptCommand(prompt: string): { type: string; id: string; message: string } {
    return {
      type: "prompt",
      id: randomUUID().slice(0, 8),
      message: prompt,
    };
  }

  private invokeRpc(args: string[], input: string): Promise<AgentOutput> {
    return new Promise((resolve) => {
      const child = spawn(this.config.piCommand, args, {
        stdio: ["pipe", "pipe", "pipe"],
        cwd: this.config.workspace || undefined,
      });
      let stdout = "";
      let stderr = "";
      let stdoutBuffer = "";
      let settled = false;

      const cleanupTimer = (): NodeJS.Timeout =>
        setTimeout(() => {
          if (!child.killed && child.exitCode === null) {
            child.kill();
          }
        }, 1_000);

      const finish = (output: AgentOutput, endStdin = true): void => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        if (endStdin && child.stdin.writable && !child.stdin.destroyed) {
          child.stdin.end();
        }
        cleanupTimer().unref();
        resolve(output);
      };

      const timeout = setTimeout(() => {
        if (!child.killed) {
          child.kill();
        }
        finish({ text: "", metadata: { error: "timeout" } }, false);
      }, this.config.timeout * 1000);

      child.stdout.setEncoding("utf-8");
      child.stderr.setEncoding("utf-8");

      child.stdout.on("data", (chunk: string | Buffer) => {
        stdoutBuffer += this.normalizeOutput(chunk);
        let newlineIndex = stdoutBuffer.indexOf("\n");
        while (newlineIndex >= 0) {
          const line = stdoutBuffer.slice(0, newlineIndex);
          stdoutBuffer = stdoutBuffer.slice(newlineIndex + 1);
          stdout += `${line}\n`;
          if (this.isTerminalRpcEvent(line)) {
            finish(this.parseOutput(stdout, 0, stderr));
            return;
          }
          newlineIndex = stdoutBuffer.indexOf("\n");
        }
      });

      child.stderr.on("data", (chunk: string | Buffer) => {
        stderr += this.normalizeOutput(chunk);
      });

      child.on("error", (err: NodeJS.ErrnoException) => {
        if (err.code === "ENOENT") {
          finish({ text: "", metadata: { error: "pi_not_found" } }, false);
          return;
        }
        finish({ text: "", metadata: { error: err.message || "unknown" } }, false);
      });

      child.on("close", (code) => {
        if (stdoutBuffer) {
          stdout += stdoutBuffer;
          stdoutBuffer = "";
        }
        finish(this.parseOutput(stdout, code ?? 1, stderr), false);
      });

      child.stdin.write(input);
    });
  }

  protected isTerminalRpcEvent(record: string): boolean {
    try {
      const event = JSON.parse(record) as {
        type?: string;
        success?: boolean;
      };
      if (event.type === "agent_end") return true;
      return event.type === "response" && event.success === false;
    } catch {
      return false;
    }
  }

  protected parseOutput(raw: string, exitCode: number, stderr: string): AgentOutput {
    const trimmed = raw.trim();
    if (!trimmed) {
      return exitCode === 0
        ? { text: "", metadata: { exitCode } }
        : {
            text: "",
            metadata: {
              error: "nonzero_exit",
              exitCode,
              stderr,
            },
          };
    }

    const textParts: string[] = [];
    let sawJsonEvent = false;

    for (const line of trimmed.split("\n")) {
      const record = line.trim();
      if (!record) continue;

      try {
        const event = JSON.parse(record) as {
          type?: string;
          success?: boolean;
          command?: string;
          error?: unknown;
          data?: { content?: unknown; session_id?: unknown; sessionId?: unknown };
          message?: { content?: unknown };
          messages?: Array<{ role?: string; content?: unknown }>;
          session_id?: unknown;
          sessionId?: unknown;
        };
        sawJsonEvent = true;
        this.updateSessionId(event);

        if (event.type === "response") {
          if (event.success === false) {
            return {
              text: "",
              metadata: {
                error: "rpc_response_error",
                rpcCommand: String(event.command ?? ""),
                rpcMessage: String(event.error ?? "unknown"),
                exitCode,
                ...(stderr ? { stderr } : {}),
              },
            };
          }

          const content = this.extractTextContent(event.data?.content);
          if (content) {
            textParts.push(content);
          }
          continue;
        }

        if (event.type === "message_end") {
          const content = this.extractTextContent(event.message?.content);
          if (content) {
            textParts.push(content);
          }
          continue;
        }

        if (event.type === "agent_end") {
          for (const message of event.messages ?? []) {
            if (message.role === "assistant") {
              const content = this.extractTextContent(message.content);
              if (content) {
                textParts.push(content);
              }
            }
          }
        }
      } catch {
        if (textParts.length === 0) {
          return exitCode === 0
            ? { text: trimmed, metadata: { exitCode } }
            : {
                text: "",
                metadata: {
                  error: "nonzero_exit",
                  exitCode,
                  ...(stderr ? { stderr } : {}),
                  stdout: trimmed,
                },
              };
        }
      }
    }

    if (textParts.length > 0) {
      return {
        text: textParts[textParts.length - 1] ?? "",
        metadata: {
          exitCode,
          ...(this._currentSessionId ? { sessionId: this._currentSessionId } : {}),
        },
      };
    }

    return exitCode === 0
      ? sawJsonEvent
        ? {
            text: "",
            metadata: {
              error: "missing_assistant_response",
              exitCode,
              stdout: trimmed,
            },
          }
        : { text: trimmed, metadata: { exitCode } }
      : {
          text: "",
          metadata: {
            error: "nonzero_exit",
            exitCode,
            ...(stderr ? { stderr } : {}),
            stdout: trimmed,
          },
        };
  }

  protected extractTextContent(content: unknown): string {
    if (typeof content === "string") {
      return content;
    }
    if (!Array.isArray(content)) {
      return "";
    }
    return content
      .map((part) => {
        if (typeof part === "string") return part;
        if (!part || typeof part !== "object") return "";
        if ("text" in part && typeof part.text === "string") return part.text;
        if ("content" in part && typeof part.content === "string") return part.content;
        return "";
      })
      .filter(Boolean)
      .join("");
  }

  protected updateSessionId(event: {
    data?: { session_id?: unknown; sessionId?: unknown };
    session_id?: unknown;
    sessionId?: unknown;
  }): void {
    const candidate =
      event.data?.session_id ?? event.data?.sessionId ?? event.session_id ?? event.sessionId;
    if (typeof candidate === "string" && candidate) {
      this._currentSessionId = candidate;
    }
  }

  protected normalizeOutput(value: string | Buffer | undefined): string {
    if (typeof value === "string") {
      return value;
    }
    if (value) {
      return value.toString("utf-8");
    }
    return "";
  }
}

type PiRpcCommand = Record<string, unknown> & { type: string; id?: string };
type PiRpcEvent = Record<string, unknown> & { type?: string };

export class PiPersistentRPCRuntime extends PiRPCRuntime {
  readonly supportsConcurrentRequests = false;
  #process: ReturnType<typeof spawn> | null = null;
  #stdoutBuffer = "";
  #stdoutLines: string[] = [];
  #stderr = "";
  #waiters: Array<() => void> = [];
  #processError: Error | null = null;
  #processExitCode: number | null = null;

  close(): void {
    const child = this.#process;
    if (!child) return;
    if (child.stdin && child.stdin.writable && !child.stdin.destroyed) {
      child.stdin.end();
    }
    if (!child.killed && child.exitCode === null) {
      child.kill();
    }
    this.#process = null;
    this.notifyWaiters();
  }

  override async generate(opts: {
    prompt: string;
    system?: string;
    schema?: Record<string, unknown>;
  }): Promise<AgentOutput> {
    void opts.schema;
    const fullPrompt = opts.system ? `${opts.system}\n\n${opts.prompt}` : opts.prompt;
    const command = this.withId(this.buildPromptCommand(fullPrompt));
    try {
      const lines = await this.collectUntil(command, (line) => this.isTerminalRpcEvent(line));
      return this.parseOutput(lines.join(""), this.#processExitCode ?? 0, this.#stderr);
    } catch (error) {
      if (error instanceof Error && error.message === "timeout") {
        this.close();
        return { text: "", metadata: { error: "timeout" } };
      }
      this.close();
      return { text: "", metadata: { error: error instanceof Error ? error.message : String(error) } };
    }
  }

  async steer(message: string): Promise<Record<string, unknown>> {
    return this.collectResponse({ type: "steer", message });
  }

  async followUp(message: string): Promise<Record<string, unknown>> {
    return this.collectResponse({ type: "follow_up", message });
  }

  async abort(): Promise<Record<string, unknown>> {
    return this.collectResponse({ type: "abort" });
  }

  async getState(): Promise<Record<string, unknown>> {
    const response = await this.collectResponse({ type: "get_state" });
    const { success: _success, ...state } = response;
    return state;
  }

  async getMessages(): Promise<Array<Record<string, unknown>>> {
    const response = await this.collectResponse({ type: "get_messages" });
    return Array.isArray(response.messages)
      ? response.messages.filter((message): message is Record<string, unknown> =>
          Boolean(message) && typeof message === "object" && !Array.isArray(message),
        )
      : [];
  }

  private ensureProcess(): ReturnType<typeof spawn> {
    if (this.#process && this.#process.exitCode === null && !this.#process.killed) {
      return this.#process;
    }
    this.#stdoutBuffer = "";
    this.#stdoutLines = [];
    this.#stderr = "";
    this.#processError = null;
    this.#processExitCode = null;

    const child = spawn(this.config.piCommand, this.buildArgs(), {
      stdio: ["pipe", "pipe", "pipe"],
      cwd: this.config.workspace || undefined,
    });
    child.stdout.setEncoding("utf-8");
    child.stderr.setEncoding("utf-8");
    child.stdout.on("data", (chunk: string | Buffer) => {
      this.pushStdout(chunk);
    });
    child.stderr.on("data", (chunk: string | Buffer) => {
      this.#stderr += this.normalizeOutput(chunk);
    });
    child.on("error", (error) => {
      this.#processError = error instanceof Error ? error : new Error(String(error));
      this.notifyWaiters();
    });
    child.on("close", (code, signal) => {
      if (this.#stdoutBuffer) {
        this.#stdoutLines.push(this.#stdoutBuffer);
        this.#stdoutBuffer = "";
      }
      this.#processExitCode = code ?? (signal ? 1 : 0);
      this.notifyWaiters();
    });
    this.#process = child;
    return child;
  }

  private pushStdout(chunk: string | Buffer): void {
    this.#stdoutBuffer += this.normalizeOutput(chunk);
    let newlineIndex = this.#stdoutBuffer.indexOf("\n");
    while (newlineIndex >= 0) {
      const line = this.#stdoutBuffer.slice(0, newlineIndex);
      this.#stdoutBuffer = this.#stdoutBuffer.slice(newlineIndex + 1);
      this.#stdoutLines.push(`${line}\n`);
      newlineIndex = this.#stdoutBuffer.indexOf("\n");
    }
    this.notifyWaiters();
  }

  private writeCommand(command: PiRpcCommand): void {
    const child = this.ensureProcess();
    const stdin = child.stdin;
    if (!stdin || !stdin.writable || stdin.destroyed) {
      throw new Error("pi RPC stdin unavailable");
    }
    stdin.write(`${JSON.stringify(command)}\n`);
  }

  private async collectUntil(command: PiRpcCommand, terminal: (line: string) => boolean): Promise<string[]> {
    this.writeCommand(command);
    const deadline = Date.now() + this.config.timeout * 1000;
    const lines: string[] = [];
    while (true) {
      const line = await this.nextLine(deadline);
      if (line === null) break;
      lines.push(line);
      if (terminal(line)) break;
    }
    return lines;
  }

  private async collectResponse(command: PiRpcCommand): Promise<Record<string, unknown>> {
    const resolved = this.withId(command);
    const lines = await this.collectUntil(resolved, (line) => {
      const event = this.loadEvent(line);
      return Boolean(event && this.isResponseFor(event, resolved));
    });

    for (const line of [...lines].reverse()) {
      const event = this.loadEvent(line);
      if (!event || !this.isResponseFor(event, resolved)) continue;
      if (event.success === false) {
        return {
          success: false,
          error: event.error ?? "",
          command: event.command ?? resolved.type,
        };
      }
      const data = event.data;
      return this.isRecord(data)
        ? { success: true, ...data }
        : { success: true, data };
    }

    return { success: false, error: "missing_rpc_response", command: resolved.type };
  }

  private async nextLine(deadline: number): Promise<string | null> {
    if (this.#stdoutLines.length > 0) {
      return this.#stdoutLines.shift() ?? null;
    }
    if (this.#processError) {
      throw this.#processError;
    }
    if (this.#processExitCode !== null) {
      return null;
    }

    const remaining = deadline - Date.now();
    if (remaining <= 0) {
      throw new Error("timeout");
    }
    await this.waitForOutput(remaining);
    if (this.#stdoutLines.length > 0) {
      return this.#stdoutLines.shift() ?? null;
    }
    if (this.#processError) {
      throw this.#processError;
    }
    if (Date.now() >= deadline) {
      throw new Error("timeout");
    }
    return null;
  }

  private waitForOutput(timeoutMs: number): Promise<void> {
    return new Promise((resolve) => {
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        this.#waiters = this.#waiters.filter((waiter) => waiter !== notify);
        resolve();
      }, timeoutMs);
      const notify = (): void => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve();
      };
      this.#waiters.push(notify);
    });
  }

  private notifyWaiters(): void {
    const waiters = this.#waiters.splice(0);
    for (const waiter of waiters) {
      waiter();
    }
  }

  private withId<T extends PiRpcCommand>(command: T): T {
    return command.id ? command : { ...command, id: randomUUID().slice(0, 8) };
  }

  private loadEvent(line: string): PiRpcEvent | null {
    try {
      const event: unknown = JSON.parse(line);
      return this.isRecord(event) ? event : null;
    } catch {
      return null;
    }
  }

  private isRecord(value: unknown): value is PiRpcEvent {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  }

  private isResponseFor(event: PiRpcEvent, command: PiRpcCommand): boolean {
    if (event.type !== "response") return false;
    if (typeof event.id === "string" && command.id) {
      return event.id === command.id;
    }
    return event.command === command.type;
  }
}
