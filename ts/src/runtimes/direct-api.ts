/**
 * Direct API runtime — uses an LLMProvider for generation/revision.
 * Port of autocontext/src/autocontext/runtimes/direct_api.py
 */

import type { LLMProvider } from "../types/index.js";
import type { AgentOutput, AgentRuntime } from "./base.js";

export class DirectAPIRuntime implements AgentRuntime {
  readonly name = "DirectAPI";

  constructor(
    private provider: LLMProvider,
    private model?: string,
  ) {}

  async generate(opts: {
    prompt: string;
    system?: string;
    schema?: Record<string, unknown>;
  }): Promise<AgentOutput> {
    const sys =
      opts.system ??
      "You are a skilled writer and analyst. Complete the task precisely.";
    const result = await this.provider.complete({
      systemPrompt: sys,
      userPrompt: opts.prompt,
      model: this.model,
    });
    return {
      text: result.text,
      costUsd: result.costUsd ?? undefined,
      model: result.model ?? undefined,
    };
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

    const sys =
      opts.system ??
      "You are revising content based on expert feedback. Improve the output.";
    const result = await this.provider.complete({
      systemPrompt: sys,
      userPrompt: revisionPrompt,
      model: this.model,
    });
    return {
      text: result.text,
      costUsd: result.costUsd ?? undefined,
      model: result.model ?? undefined,
    };
  }
}
