/**
 * RLM Session — drives the multi-turn REPL conversation loop for one agent role.
 * Mirrors Python autocontext.harness.repl.session.RlmSession.
 */

import type { ReplWorker, LlmComplete, ExecutionRecord } from "./types.js";

export interface RlmSessionOpts {
  complete: LlmComplete;
  worker: ReplWorker;
  role: string;
  model: string;
  systemPrompt: string;
  initialUserMessage?: string;
  maxTurns?: number;
  maxTokensPerTurn?: number;
  temperature?: number;
  onTurn?: (current: number, total: number, ready: boolean) => void;
}

export interface RlmResult {
  content: string;
  executionHistory: ExecutionRecord[];
  turnsUsed: number;
}

/** Extract code from the first code block delimited by code tags. */
export function extractCode(text: string): string | null {
  const match = text.match(/<code>([\s\S]*?)<\/code>/);
  return match ? match[1].trim() : null;
}

/**
 * Drives the multi-turn REPL conversation loop for one agent role.
 *
 * Flow per turn:
 * 1. Send conversation history to LLM
 * 2. Extract code block from response
 * 3. Run code via worker
 * 4. Build feedback from stdout/error
 * 5. Check answer["ready"] flag — if true, exit loop
 * 6. Repeat until maxTurns
 */
export class RlmSession {
  private readonly complete: LlmComplete;
  private readonly worker: ReplWorker;
  private readonly role: string;
  private readonly model: string;
  private readonly systemPrompt: string;
  private readonly initialUserMessage: string;
  private readonly maxTurns: number;
  private readonly maxTokensPerTurn: number;
  private readonly temperature: number;
  private readonly onTurn?: RlmSessionOpts["onTurn"];

  constructor(opts: RlmSessionOpts) {
    this.complete = opts.complete;
    this.worker = opts.worker;
    this.role = opts.role;
    this.model = opts.model;
    this.systemPrompt = opts.systemPrompt;
    this.initialUserMessage = opts.initialUserMessage ?? "Begin exploring the data.";
    this.maxTurns = opts.maxTurns ?? 15;
    this.maxTokensPerTurn = opts.maxTokensPerTurn ?? 2048;
    this.temperature = opts.temperature ?? 0.2;
    this.onTurn = opts.onTurn;
  }

  /** Run the full REPL loop and return an RlmResult. */
  async run(): Promise<RlmResult> {
    const messages: Array<{ role: string; content: string }> = [
      { role: "user", content: this.initialUserMessage },
    ];
    const executionHistory: ExecutionRecord[] = [];
    let finalContent = "";
    let answeredReady = false;

    for (let turn = 1; turn <= this.maxTurns; turn++) {
      const response = await this.complete(messages, {
        model: this.model,
        maxTokens: this.maxTokensPerTurn,
        temperature: this.temperature,
        systemPrompt: this.systemPrompt,
      });

      const assistantText = response.text;
      messages.push({ role: "assistant", content: assistantText });

      const code = extractCode(assistantText);
      if (code === null) {
        messages.push({
          role: "user",
          content:
            'Please write code inside <code> tags to continue your analysis, or set answer["ready"] = True to finalize.',
        });
        this.onTurn?.(turn, this.maxTurns, false);
        continue;
      }

      const result = await this.worker.runCode({ code });
      const answerReady = result.answer?.["ready"] === true;

      executionHistory.push({
        turn,
        code,
        stdout: result.stdout,
        error: result.error,
        answerReady,
      });

      const parts: string[] = [];
      if (result.stdout) {
        parts.push(`Output:\n${result.stdout}`);
      }
      if (result.error) {
        parts.push(`Error:\n${result.error}`);
      }
      if (parts.length === 0) {
        parts.push("(no output)");
      }
      const feedback = parts.join("\n");

      this.onTurn?.(turn, this.maxTurns, answerReady);

      if (answerReady) {
        finalContent = String(result.answer?.["content"] ?? "");
        answeredReady = true;
        break;
      }

      messages.push({ role: "user", content: feedback });
    }

    if (!answeredReady && this.worker.namespace?.["answer"]) {
      const ans = this.worker.namespace["answer"] as Record<string, unknown>;
      finalContent = String(ans?.["content"] ?? "");
    }

    return {
      content: finalContent,
      executionHistory,
      turnsUsed: executionHistory.length,
    };
  }
}
