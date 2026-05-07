import type { RuntimeCommandGrant } from "./workspace-env.js";
import type { AgentOutput, AgentRuntime } from "./base.js";
import { agentOutputMetadata } from "./agent-output-metadata.js";
import type { RuntimeSession } from "../session/runtime-session.js";

export interface RuntimeSessionAgentRuntimeOpts {
  runtime: AgentRuntime;
  session: RuntimeSession;
  role?: string;
  cwd?: string;
  commands?: RuntimeCommandGrant[];
}

export class RuntimeSessionAgentRuntime implements AgentRuntime {
  readonly name: string;
  #runtime: AgentRuntime;
  #session: RuntimeSession;
  #role: string;
  #cwd?: string;
  #commands?: RuntimeCommandGrant[];

  constructor(opts: RuntimeSessionAgentRuntimeOpts) {
    this.#runtime = opts.runtime;
    this.#session = opts.session;
    this.#role = opts.role ?? "agent-runtime";
    this.#cwd = opts.cwd;
    this.#commands = opts.commands;
    this.name = `RuntimeSession(${opts.runtime.name})`;
  }

  async generate(opts: {
    prompt: string;
    system?: string;
    schema?: Record<string, unknown>;
  }): Promise<AgentOutput> {
    return this.#record("generate", opts.prompt, () => this.#runtime.generate(opts));
  }

  async revise(opts: {
    prompt: string;
    previousOutput: string;
    feedback: string;
    system?: string;
  }): Promise<AgentOutput> {
    return this.#record("revise", opts.prompt, () => this.#runtime.revise(opts));
  }

  close(): void {
    this.#runtime.close?.();
  }

  async #record(
    operation: string,
    prompt: string,
    run: () => Promise<AgentOutput>,
  ): Promise<AgentOutput> {
    let output: AgentOutput | undefined;
    let failure: unknown;
    const result = await this.#session.submitPrompt({
      prompt,
      role: this.#role,
      cwd: this.#cwd,
      commands: this.#commands,
      handler: async () => {
        try {
          output = await run();
        } catch (error) {
          failure = error;
          throw error;
        }
        return {
          text: output.text,
          metadata: agentOutputMetadata(this.#runtime.name, output, {
            operation,
            runtimeSessionId: this.#session.sessionId,
          }),
        };
      },
    });

    if (result.isError) {
      throw failure ?? new Error(result.error);
    }

    if (!output) {
      return {
        text: result.text,
        metadata: {
          runtime: this.#runtime.name,
          runtimeSessionId: this.#session.sessionId,
        },
      };
    }

    return {
      ...output,
      metadata: {
        ...(output.metadata ?? {}),
        runtimeSessionId: this.#session.sessionId,
      },
    };
  }
}
