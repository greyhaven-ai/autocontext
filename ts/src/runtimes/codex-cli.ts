/**
 * Codex CLI runtime — wraps `codex exec` for agent execution (AC-345 Task 17).
 * Mirrors Python's autocontext/runtimes/codex_cli.py.
 */

import { execFileSync } from "node:child_process";
import type { AgentOutput } from "./index.js";
import { definedConfigOptions } from "./config-options.js";

export interface CodexCLIConfigOpts {
  model?: string;
  approvalMode?: string;
  timeout?: number;
  workspace?: string;
  quiet?: boolean;
  extraArgs?: string[];
}

const CODEX_CLI_CONFIG_DEFAULTS = {
  model: "o4-mini",
  approvalMode: "full-auto",
  timeout: 120.0,
  workspace: "",
  quiet: false,
  extraArgs: [] as string[],
};

export class CodexCLIConfig {
  readonly model!: string;
  readonly approvalMode!: string;
  readonly timeout!: number;
  readonly workspace!: string;
  readonly quiet!: boolean;
  readonly extraArgs!: string[];

  constructor(opts: CodexCLIConfigOpts = {}) {
    Object.assign(this, {
      ...CODEX_CLI_CONFIG_DEFAULTS,
      ...definedConfigOptions(opts),
      extraArgs: [...(opts.extraArgs ?? CODEX_CLI_CONFIG_DEFAULTS.extraArgs)],
    });
  }
}

export class CodexCLIRuntime {
  #config: CodexCLIConfig;

  constructor(config?: CodexCLIConfig) {
    this.#config = config ?? new CodexCLIConfig();
  }

  readonly name = "codex-cli";

  async generate(opts: {
    prompt: string;
    system?: string;
    schema?: Record<string, unknown>;
  }): Promise<AgentOutput> {
    const args = this.buildArgs(opts.schema);
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
      `Produce an improved version:`;
    return this.#invoke(revisionPrompt, this.buildArgs());
  }

  buildArgs(schema?: Record<string, unknown>): string[] {
    const args = ["exec"];
    args.push("--model", this.#config.model);

    if (this.#config.approvalMode === "full-auto") {
      args.push("--full-auto");
    }
    if (this.#config.quiet) {
      args.push("--quiet");
    }
    if (this.#config.workspace) {
      args.push("--cd", this.#config.workspace);
    }
    if (schema) {
      args.push("--output-schema", JSON.stringify(schema));
    }
    args.push(...this.#config.extraArgs);
    return args;
  }

  parseOutput(raw: string): AgentOutput {
    const lines = raw.trim().split("\n");
    if (lines.length === 0 || (lines.length === 1 && !lines[0].trim())) {
      return { text: "", metadata: {} };
    }

    const messages: string[] = [];
    let isJsonl = false;

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        const event = JSON.parse(trimmed);
        isJsonl = true;
        if (typeof event === "object" && event !== null) {
          const etype = event.type ?? "";
          if (etype === "item.message" && Array.isArray(event.content)) {
            for (const block of event.content) {
              if (typeof block === "object" && block !== null && "text" in block) {
                messages.push(block.text);
              }
            }
          } else if ("text" in event) {
            messages.push(event.text);
          }
        }
      } catch {
        if (!isJsonl) {
          return { text: raw.trim(), metadata: {} };
        }
      }
    }

    if (messages.length > 0) {
      return { text: messages.join("\n"), metadata: {} };
    }
    return { text: raw.trim(), metadata: {} };
  }

  async #invoke(prompt: string, args: string[]): Promise<AgentOutput> {
    try {
      const stdout = execFileSync("codex", [...args, prompt], {
        timeout: this.#config.timeout * 1000,
        encoding: "utf-8",
        stdio: ["pipe", "pipe", "pipe"],
      });
      return this.parseOutput(stdout);
    } catch (err: unknown) {
      const error = err as { message?: string; code?: string };
      if (error.code === "ETIMEDOUT") {
        return { text: "", metadata: { error: "timeout" } };
      }
      return { text: "", metadata: { error: error.message ?? "unknown" } };
    }
  }
}
