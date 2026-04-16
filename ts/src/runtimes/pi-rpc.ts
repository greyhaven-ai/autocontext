/**
 * Pi RPC runtime — subprocess stdin/stdout JSONL communication with Pi.
 * Mirrors Python's autocontext/runtimes/pi_rpc.py.
 */

import { randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import type { AgentOutput } from "./base.js";

export interface PiRPCConfigOpts {
  piCommand?: string;
  model?: string;
  timeout?: number;
  sessionPersistence?: boolean;
  noContextFiles?: boolean;
  extraArgs?: string[];
}

export class PiRPCConfig {
  readonly piCommand: string;
  readonly model: string;
  readonly timeout: number;
  readonly sessionPersistence: boolean;
  readonly noContextFiles: boolean;
  readonly extraArgs: string[];

  constructor(opts: PiRPCConfigOpts = {}) {
    this.piCommand = opts.piCommand ?? "pi";
    this.model = opts.model ?? "";
    this.timeout = opts.timeout ?? 120.0;
    this.sessionPersistence = opts.sessionPersistence ?? true;
    this.noContextFiles = opts.noContextFiles ?? false;
    this.extraArgs = [...(opts.extraArgs ?? [])];
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

  private buildArgs(): string[] {
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

  private buildPromptCommand(prompt: string): { type: string; id: string; message: string } {
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

  private isTerminalRpcEvent(record: string): boolean {
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

  private parseOutput(raw: string, exitCode: number, stderr: string): AgentOutput {
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

  private extractTextContent(content: unknown): string {
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

  private updateSessionId(event: {
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

  private normalizeOutput(value: string | Buffer | undefined): string {
    if (typeof value === "string") {
      return value;
    }
    if (value) {
      return value.toString("utf-8");
    }
    return "";
  }
}
