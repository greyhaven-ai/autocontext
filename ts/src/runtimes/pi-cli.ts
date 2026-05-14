/**
 * Pi CLI runtime — wraps `pi --print` for agent execution (AC-361).
 * Mirrors Python's autocontext/runtimes/pi_cli.py.
 */

import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
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

const TIMEOUT_KILL_GRACE_MS = 5_000;
const MANAGED_EXIT_SIGNALS: NodeJS.Signals[] = ["SIGINT", "SIGTERM"];

interface PiCLIProcessResult {
  stdout: string;
  stderr: string;
  exitCode: number | null;
  signal: NodeJS.Signals | null;
  timedOut: boolean;
  error?: Error;
}

interface RunPiCLIOptions {
  input: string;
  timeoutMs: number;
  cwd?: string;
  graceMs?: number;
}

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
      "Produce an improved version:";
    return this.#invoke(revisionPrompt);
  }

  parseOutput(raw: string): AgentOutput {
    const trimmed = raw.trim();
    if (!trimmed) return { text: "", metadata: {} };
    return { text: trimmed, metadata: {} };
  }

  async #invoke(prompt: string): Promise<AgentOutput> {
    const args = ["--print"];
    if (this.#config.model) {
      args.push("--model", this.#config.model);
    }
    if (this.#config.noContextFiles) {
      args.push("--no-context-files");
    }

    const result = await runPiCLIWithGroupKill(this.#config.piCommand, args, {
      input: prompt,
      timeoutMs: this.#config.timeout * 1000,
      cwd: this.#config.workspace || undefined,
    });

    if (result.timedOut) {
      return {
        text: "",
        metadata: { error: "timeout", timeoutSeconds: this.#config.timeout },
      };
    }
    if (result.error) {
      return { text: "", metadata: { error: result.error.message || "unknown" } };
    }
    if (result.exitCode !== 0 && !result.stdout.trim()) {
      return {
        text: "",
        metadata: {
          error: "nonzero_exit",
          exitCode: result.exitCode,
          signal: result.signal,
          stderr: result.stderr.slice(0, 500),
        },
      };
    }

    return this.parseOutput(result.stdout);
  }
}

function runPiCLIWithGroupKill(
  command: string,
  args: string[],
  opts: RunPiCLIOptions,
): Promise<PiCLIProcessResult> {
  return new Promise((resolve) => {
    let child: ChildProcessWithoutNullStreams;
    try {
      child = spawn(command, args, {
        cwd: opts.cwd,
        detached: process.platform !== "win32",
        stdio: ["pipe", "pipe", "pipe"],
      });
    } catch (err: unknown) {
      resolve({
        stdout: "",
        stderr: "",
        exitCode: null,
        signal: null,
        timedOut: false,
        error: toError(err),
      });
      return;
    }

    let stdout = "";
    let stderr = "";
    let timedOut = false;
    let settled = false;
    let timeoutHandle: ReturnType<typeof setTimeout> | undefined;
    let graceHandle: ReturnType<typeof setTimeout> | undefined;
    let removeProcessHandlers = (): void => {};

    const cleanupActiveChild = (): void => {
      killProcessGroup(child);
      closeChildStdio(child);
    };
    const signalHandlers = new Map<NodeJS.Signals, () => void>();
    const onProcessExit = (): void => {
      cleanupActiveChild();
    };
    for (const signal of MANAGED_EXIT_SIGNALS) {
      const handler = (): void => {
        cleanupActiveChild();
        removeProcessHandlers();
        reraiseSignal(signal);
      };
      signalHandlers.set(signal, handler);
      process.once(signal, handler);
    }
    process.once("exit", onProcessExit);
    removeProcessHandlers = (): void => {
      for (const [signal, handler] of signalHandlers) {
        process.off(signal, handler);
      }
      signalHandlers.clear();
      process.off("exit", onProcessExit);
    };

    const settle = (result: Omit<PiCLIProcessResult, "stdout" | "stderr" | "timedOut">): void => {
      if (settled) return;
      settled = true;
      if (timeoutHandle) clearTimeout(timeoutHandle);
      if (graceHandle) clearTimeout(graceHandle);
      removeProcessHandlers();
      closeChildStdio(child);
      resolve({
        stdout,
        stderr,
        timedOut,
        ...result,
      });
    };

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string | Buffer) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk: string | Buffer) => {
      stderr += chunk.toString();
    });
    child.on("error", (err) => {
      settle({ exitCode: null, signal: null, error: toError(err) });
    });
    child.on("close", (code, signal) => {
      settle({ exitCode: code, signal });
    });
    child.stdin.on("error", () => {
      // Child may exit before it consumes the prompt; close/error metadata will
      // arrive via the process events above.
    });

    timeoutHandle = setTimeout(() => {
      timedOut = true;
      killProcessGroup(child);
      closeChildStdio(child);
      graceHandle = setTimeout(() => {
        settle({ exitCode: child.exitCode, signal: child.signalCode });
      }, opts.graceMs ?? TIMEOUT_KILL_GRACE_MS);
    }, opts.timeoutMs);

    if (opts.input) {
      child.stdin.write(opts.input);
    }
    child.stdin.end();
  });
}

function killProcessGroup(child: ChildProcessWithoutNullStreams): void {
  if (process.platform !== "win32" && child.pid !== undefined) {
    try {
      process.kill(-child.pid, "SIGKILL");
      return;
    } catch {
      // Fall back to killing the direct child if the process group is already
      // gone or the platform rejects negative PIDs.
    }
  }

  child.kill("SIGKILL");
}

function closeChildStdio(child: ChildProcessWithoutNullStreams): void {
  child.stdin.destroy();
  child.stdout.destroy();
  child.stderr.destroy();
}

function reraiseSignal(signal: NodeJS.Signals): void {
  try {
    process.kill(process.pid, signal);
  } catch {
    process.exit(signalToExitCode(signal));
  }
}

function signalToExitCode(signal: NodeJS.Signals): number {
  if (signal === "SIGINT") return 130;
  if (signal === "SIGTERM") return 143;
  return 1;
}

function toError(err: unknown): Error {
  return err instanceof Error ? err : new Error(String(err));
}
