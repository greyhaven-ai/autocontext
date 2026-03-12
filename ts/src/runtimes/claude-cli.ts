/**
 * Claude Code CLI runtime — wraps `claude -p` for agent execution.
 * Port of autocontext/src/autocontext/runtimes/claude_cli.py
 */

import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { promisify } from "node:util";
import { which } from "../util.js";
import type { AgentOutput, AgentRuntime } from "./base.js";

const execFileAsync = promisify(execFile);

export interface ClaudeCLIConfig {
  model?: string;
  fallbackModel?: string;
  tools?: string;
  permissionMode?: string;
  sessionPersistence?: boolean;
  sessionId?: string;
  timeout?: number;
  systemPrompt?: string;
  appendSystemPrompt?: string;
  extraArgs?: string[];
}

export class ClaudeCLIRuntime implements AgentRuntime {
  readonly name = "ClaudeCLI";
  private config: Required<
    Pick<ClaudeCLIConfig, "model" | "permissionMode" | "timeout">
  > &
    ClaudeCLIConfig;
  private _totalCost = 0;
  private _claudePath: string | null;

  constructor(config?: ClaudeCLIConfig) {
    this.config = {
      model: "sonnet",
      permissionMode: "bypassPermissions",
      timeout: 120_000,
      ...config,
    };
    this._claudePath = which("claude");
  }

  get available(): boolean {
    return this._claudePath !== null;
  }

  get totalCost(): number {
    return this._totalCost;
  }

  async generate(opts: {
    prompt: string;
    system?: string;
    schema?: Record<string, unknown>;
  }): Promise<AgentOutput> {
    const args = this.buildArgs(opts.system, opts.schema);
    return this.invoke(opts.prompt, args);
  }

  async revise(opts: {
    prompt: string;
    previousOutput: string;
    feedback: string;
    system?: string;
  }): Promise<AgentOutput> {
    const revisionPrompt =
      `Revise the following output based on the judge's feedback.\n\n` +
      `## Original Output\n${opts.previousOutput}\n\n` +
      `## Judge Feedback\n${opts.feedback}\n\n` +
      `## Original Task\n${opts.prompt}\n\n` +
      "Produce an improved version:";
    const args = this.buildArgs(opts.system);
    return this.invoke(revisionPrompt, args);
  }

  private buildArgs(
    system?: string,
    schema?: Record<string, unknown>,
  ): string[] {
    const claude = this._claudePath ?? "claude";
    const args = ["-p", "--output-format", "json"];

    args.push("--model", this.config.model);
    if (this.config.fallbackModel) {
      args.push("--fallback-model", this.config.fallbackModel);
    }
    if (this.config.tools != null) {
      args.push("--tools", this.config.tools);
    }
    args.push("--permission-mode", this.config.permissionMode);

    if (!this.config.sessionPersistence) {
      args.push("--no-session-persistence");
    }
    if (this.config.sessionId) {
      args.push("--session-id", this.config.sessionId);
    }

    if (system) {
      args.push("--system-prompt", system);
    } else if (this.config.systemPrompt) {
      args.push("--system-prompt", this.config.systemPrompt);
    }
    if (this.config.appendSystemPrompt) {
      args.push("--append-system-prompt", this.config.appendSystemPrompt);
    }
    if (schema) {
      args.push("--json-schema", JSON.stringify(schema));
    }
    if (this.config.extraArgs) {
      for (const arg of this.config.extraArgs) {
        if (typeof arg !== "string") {
          throw new Error(`extraArgs must be strings, got ${typeof arg}`);
        }
      }
      args.push(...this.config.extraArgs);
    }

    return args;
  }

  private async invoke(prompt: string, args: string[]): Promise<AgentOutput> {
    const claude = this._claudePath ?? "claude";
    args.push(prompt);

    try {
      const { stdout } = await execFileAsync(claude, args, {
        timeout: this.config.timeout,
        maxBuffer: 10 * 1024 * 1024,
        encoding: "utf8",
      });
      return this.parseOutput(stdout);
    } catch (err: unknown) {
      if (err && typeof err === "object" && "killed" in err) {
        return { text: "", metadata: { error: "timeout" } };
      }
      const e = err as { stdout?: string; code?: string };
      if (e.code === "ENOENT") {
        return { text: "", metadata: { error: "claude_not_found" } };
      }
      if (e.stdout) return this.parseOutput(e.stdout);
      return { text: "", metadata: { error: String(err) } };
    }
  }

  private parseOutput(raw: string): AgentOutput {
    try {
      const data = JSON.parse(raw);
      const cost = data.total_cost_usd;
      if (cost != null) this._totalCost += cost;

      const modelUsage = data.modelUsage ?? {};
      const model = Object.keys(modelUsage)[0];

      return {
        text: data.result ?? "",
        structured: data.structured_output,
        costUsd: cost,
        model,
        sessionId: data.session_id,
        metadata: {
          durationMs: data.duration_ms,
          durationApiMs: data.duration_api_ms,
          numTurns: data.num_turns,
          isError: data.is_error ?? false,
          usage: data.usage ?? {},
        },
      };
    } catch {
      return { text: raw.trim() };
    }
  }
}

export function createSessionRuntime(opts?: {
  model?: string;
  tools?: string;
  systemPrompt?: string;
}): ClaudeCLIRuntime {
  return new ClaudeCLIRuntime({
    model: opts?.model ?? "sonnet",
    tools: opts?.tools,
    sessionId: randomUUID(),
    sessionPersistence: true,
    systemPrompt: opts?.systemPrompt,
  });
}
