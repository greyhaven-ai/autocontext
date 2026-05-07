/**
 * Pi CLI runtime — wraps `pi --print` for agent execution (AC-361).
 * Mirrors Python's autocontext/runtimes/pi_cli.py.
 */

import { execFileSync } from "node:child_process";
import type { AgentOutput } from "./base.js";
import { definedConfigOptions } from "./config-options.js";

export interface PiCLIConfigOpts {
  piCommand?: string;
  model?: string;
  timeout?: number;
  workspace?: string;
  noContextFiles?: boolean;
}

const PI_CLI_CONFIG_DEFAULTS = {
  piCommand: "pi",
  model: "",
  timeout: 300.0,
  workspace: "",
  noContextFiles: false,
};

export class PiCLIConfig {
  readonly piCommand!: string;
  readonly model!: string;
  readonly timeout!: number;
  readonly workspace!: string;
  readonly noContextFiles!: boolean;

  constructor(opts: PiCLIConfigOpts = {}) {
    Object.assign(this, { ...PI_CLI_CONFIG_DEFAULTS, ...definedConfigOptions(opts) });
  }
}

export class PiCLIRuntime {
  readonly name = "pi-cli";
  #config: PiCLIConfig;

  constructor(config?: PiCLIConfig) {
    this.#config = config ?? new PiCLIConfig();
  }

  async generate(opts: { prompt: string; system?: string }): Promise<AgentOutput> {
    const fullPrompt = opts.system ? `${opts.system}\n\n${opts.prompt}` : opts.prompt;
    return this.#invoke(fullPrompt);
  }

  async revise(opts: {
    prompt: string;
    previousOutput: string;
    feedback: string;
  }): Promise<AgentOutput> {
    const revisionPrompt =
      `Revise the following output based on the judge's feedback.\n\n` +
      `## Original Output\n${opts.previousOutput}\n\n` +
      `## Judge Feedback\n${opts.feedback}\n\n` +
      `## Original Task\n${opts.prompt}\n\n` +
      `Produce an improved version:`;
    return this.#invoke(revisionPrompt);
  }

  parseOutput(raw: string): AgentOutput {
    const trimmed = raw.trim();
    if (!trimmed) return { text: "", metadata: {} };
    return { text: trimmed, metadata: {} };
  }

  #invoke(prompt: string): AgentOutput {
    const args = ["--print"];
    if (this.#config.model) {
      args.push("--model", this.#config.model);
    }
    if (this.#config.noContextFiles) {
      args.push("--no-context-files");
    }

    try {
      const stdout = execFileSync(this.#config.piCommand, args, {
        input: prompt,
        timeout: this.#config.timeout * 1000,
        encoding: "utf-8",
        stdio: ["pipe", "pipe", "pipe"],
        cwd: this.#config.workspace || undefined,
      });
      return this.parseOutput(stdout);
    } catch (err: unknown) {
      const error = err as { code?: string; message?: string };
      if (error.code === "ETIMEDOUT") {
        return { text: "", metadata: { error: "timeout" } };
      }
      return { text: "", metadata: { error: error.message ?? "unknown" } };
    }
  }
}
