/**
 * Claude Code CLI runtime — wraps `claude -p` for agent execution.
 * Port of autocontext/src/autocontext/runtimes/claude_cli.py
 */

import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { promisify } from "node:util";
import { which } from "../util.js";
import type { EventStreamEmitter } from "../loop/events.js";
import {
  RepairGate,
  repairGateActiveFor,
  type RepairContext,
  type RepairGateConfig,
} from "../harness-optimization/repair-gate.js";
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
  // Opt-in AC-878 repair gate. All three must be present AND the gate active for
  // `repairScenario` for a malformed CLI JSON envelope to be structurally
  // repaired before the raw-text fallback. Absent (the default) => the parse
  // path is byte-unchanged.
  repairGate?: RepairGateConfig;
  repairScenario?: string;
  repairEmitter?: EventStreamEmitter;
}

export class ClaudeCLIRuntime implements AgentRuntime {
  readonly name = "ClaudeCLI";
  #config: Required<Pick<ClaudeCLIConfig, "model" | "permissionMode" | "timeout">> &
    ClaudeCLIConfig;
  #totalCost = 0;
  #claudePath: string | null;

  constructor(config?: ClaudeCLIConfig) {
    this.#config = {
      model: "sonnet",
      permissionMode: "bypassPermissions",
      timeout: 600_000,
      ...config,
    };
    this.#claudePath = which("claude");
  }

  get available(): boolean {
    return this.#claudePath !== null;
  }

  get totalCost(): number {
    return this.#totalCost;
  }

  async generate(opts: {
    prompt: string;
    system?: string;
    schema?: Record<string, unknown>;
  }): Promise<AgentOutput> {
    const args = this.#buildArgs(opts.system, opts.schema);
    return this.#invoke(opts.prompt, args);
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
    const args = this.#buildArgs(opts.system);
    return this.#invoke(revisionPrompt, args);
  }

  #buildArgs(system?: string, schema?: Record<string, unknown>): string[] {
    const args = ["-p", "--output-format", "json"];

    args.push("--model", this.#config.model);
    if (this.#config.fallbackModel) {
      args.push("--fallback-model", this.#config.fallbackModel);
    }
    if (this.#config.tools != null) {
      args.push("--tools", this.#config.tools);
    }
    args.push("--permission-mode", this.#config.permissionMode);

    if (!this.#config.sessionPersistence) {
      args.push("--no-session-persistence");
    }
    if (this.#config.sessionId) {
      args.push("--session-id", this.#config.sessionId);
    }

    if (system) {
      args.push("--system-prompt", system);
    } else if (this.#config.systemPrompt) {
      args.push("--system-prompt", this.#config.systemPrompt);
    }
    if (this.#config.appendSystemPrompt) {
      args.push("--append-system-prompt", this.#config.appendSystemPrompt);
    }
    if (schema) {
      args.push("--json-schema", JSON.stringify(schema));
    }
    if (this.#config.extraArgs) {
      for (const arg of this.#config.extraArgs) {
        if (typeof arg !== "string") {
          throw new Error(`extraArgs must be strings, got ${typeof arg}`);
        }
      }
      args.push(...this.#config.extraArgs);
    }

    return args;
  }

  async #invoke(prompt: string, args: string[]): Promise<AgentOutput> {
    const claude = this.#claudePath ?? "claude";
    args.push(prompt);

    try {
      const { stdout } = await execFileAsync(claude, args, {
        timeout: this.#config.timeout,
        maxBuffer: 10 * 1024 * 1024,
        encoding: "utf8",
      });
      return this.#parseOutput(stdout);
    } catch (err: unknown) {
      if (err && typeof err === "object" && "killed" in err) {
        return { text: "", metadata: { error: "timeout" } };
      }
      const e = err as { stdout?: string; code?: string };
      if (e.code === "ENOENT") {
        return { text: "", metadata: { error: "claude_not_found" } };
      }
      if (e.stdout) return this.#parseOutput(e.stdout);
      return { text: "", metadata: { error: String(err) } };
    }
  }

  #parseOutput(raw: string): AgentOutput {
    try {
      return this.#buildOutput(JSON.parse(raw));
    } catch {
      // Opt-in AC-878 seam: try a structural repair of the malformed envelope
      // before falling back to raw text. Default (no repair config) => the gate
      // is never consulted and this is byte-identical to the old fallback.
      const repaired = this.#tryRepairEnvelope(raw);
      if (repaired !== null) {
        try {
          return this.#buildOutput(JSON.parse(repaired));
        } catch {
          // repaired string still did not parse; fall through to text fallback.
        }
      }
      return { text: raw.trim() };
    }
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  #buildOutput(data: any): AgentOutput {
    const cost = data.total_cost_usd;
    if (cost != null) this.#totalCost += cost;

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
  }

  #tryRepairEnvelope(raw: string): string | null {
    return repairCliEnvelope(raw, {
      gate: this.#config.repairGate,
      scenario: this.#config.repairScenario,
      emitter: this.#config.repairEmitter,
    });
  }
}

/**
 * Run the opt-in AC-878 repair gate over a malformed CLI JSON envelope, returning
 * the structurally repaired string or null. Returns null (a no-op) unless all
 * three repair fields are present AND `repairGateActiveFor` says the gate is
 * active for the scenario, so the default parse path stays byte-unchanged. When
 * active it runs the gate over the raw string and emits one event per repair.
 */
export function repairCliEnvelope(
  raw: string,
  opts: {
    gate?: RepairGateConfig;
    scenario?: string;
    emitter?: EventStreamEmitter;
  },
): string | null {
  const { gate, scenario, emitter } = opts;
  if (gate === undefined || scenario === undefined || emitter === undefined) return null;
  if (!repairGateActiveFor(gate, scenario)) return null;
  const ctx: RepairContext = { toolCallJson: raw };
  new RepairGate(emitter).run(scenario, ctx);
  return ctx.repairedToolCallJson ?? null;
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
