import type { AgentTaskResult, LLMProvider } from "../types/index.js";
import { RlmSession } from "./session.js";
import { SecureExecReplWorker } from "./secure-exec-worker.js";
import type { LlmComplete, RlmPhase, RlmSessionRecord, RlmTaskConfig } from "./types.js";

export interface AgentTaskRlmOpts {
  provider: LLMProvider;
  model: string;
  config: RlmTaskConfig;
  phase: RlmPhase;
  taskPrompt: string;
  rubric: string;
  referenceContext?: string;
  requiredConcepts?: string[];
  currentOutput?: string;
  judgeResult?: AgentTaskResult;
  revisionPrompt?: string;
}

function makeConversationPrompt(messages: Array<{ role: string; content: string }>): string {
  const transcript = messages
    .map((message, index) => `### ${message.role.toUpperCase()} ${index + 1}\n${message.content}`)
    .join("\n\n");

  return (
    "Continue the REPL-loop session below.\n\n" +
    `${transcript}\n\n` +
    "Return JavaScript inside <code>...</code> tags. " +
    "When you are done, set answer.ready = true and answer.content to the final output."
  );
}

function makeProviderComplete(provider: LLMProvider): LlmComplete {
  return async (messages, opts) => provider.complete({
    systemPrompt: opts?.systemPrompt ?? "",
    userPrompt: makeConversationPrompt(messages),
    model: opts?.model,
    temperature: opts?.temperature,
    maxTokens: opts?.maxTokens,
  });
}

function buildSystemPrompt(opts: AgentTaskRlmOpts): string {
  const phaseLabel = opts.phase === "generate" ? "draft the first answer" : "revise the current answer";
  const references = [
    "- taskPrompt: the task instructions",
    "- rubric: the evaluation rubric",
    "- referenceContext: authoritative context for fact-checking (may be empty)",
    "- requiredConcepts: concepts that should be covered",
    "- currentOutput: the current draft when revising",
    "- judgeFeedback: latest judge score/reasoning/dimensions when revising",
    "- state: persistent JSON-serializable scratchpad across turns",
    "- answer: { ready: boolean, content: string } used for the final answer",
  ];

  return [
    `You are using REPL-loop mode to ${phaseLabel}.`,
    "You may inspect the provided variables by writing JavaScript inside <code> tags.",
    "The sandbox is intentionally restricted: no filesystem writes, no network, no child_process, and no environment access.",
    `You have up to ${opts.config.maxTurns} turns. Stdout is truncated at ${opts.config.maxStdoutChars} characters per turn.`,
    "Available variables:",
    ...references,
    "Available helpers:",
    "- peek(text, start, length)",
    "- grep(text, pattern, context)",
    "- chunkBySize(text, size, overlap)",
    "- chunkByHeaders(text)",
    "Use console.log for intermediate inspection.",
    "When ready, set answer.ready = true and answer.content to ONLY the final output text.",
  ].join("\n");
}

function buildInitialMessage(opts: AgentTaskRlmOpts): string {
  const lines = [
    `Task prompt:\n${opts.taskPrompt}`,
    `Rubric:\n${opts.rubric}`,
  ];

  if (opts.referenceContext) {
    lines.push(`Reference context:\n${opts.referenceContext}`);
  }
  if (opts.requiredConcepts && opts.requiredConcepts.length > 0) {
    lines.push(`Required concepts: ${opts.requiredConcepts.join(", ")}`);
  }
  if (opts.phase === "revise") {
    lines.push(`Current output:\n${opts.currentOutput ?? ""}`);
    if (opts.judgeResult) {
      lines.push(
        "Judge feedback:\n" +
        JSON.stringify(
          {
            score: opts.judgeResult.score,
            reasoning: opts.judgeResult.reasoning,
            dimensionScores: opts.judgeResult.dimensionScores,
          },
          null,
          2,
        ),
      );
    }
    if (opts.revisionPrompt) {
      lines.push(`Revision instruction:\n${opts.revisionPrompt}`);
    }
    lines.push("Use the evidence above to produce a stronger revision.");
  } else {
    lines.push("Produce the strongest initial answer you can.");
  }

  return lines.join("\n\n");
}

function buildNamespace(opts: AgentTaskRlmOpts): Record<string, unknown> {
  return {
    taskPrompt: opts.taskPrompt,
    rubric: opts.rubric,
    referenceContext: opts.referenceContext ?? "",
    requiredConcepts: opts.requiredConcepts ?? [],
    currentOutput: opts.currentOutput ?? "",
    judgeFeedback: opts.judgeResult
      ? {
          score: opts.judgeResult.score,
          reasoning: opts.judgeResult.reasoning,
          dimensionScores: opts.judgeResult.dimensionScores,
        }
      : null,
    revisionPrompt: opts.revisionPrompt ?? "",
    answer: { ready: false, content: "" },
    state: {},
  };
}

export async function runAgentTaskRlmSession(opts: AgentTaskRlmOpts): Promise<RlmSessionRecord> {
  const worker = new SecureExecReplWorker({
    namespace: buildNamespace(opts),
    maxStdoutChars: opts.config.maxStdoutChars,
    codeTimeoutMs: opts.config.codeTimeoutMs,
    memoryLimitMb: opts.config.memoryLimitMb,
  });

  try {
    const session = new RlmSession({
      complete: makeProviderComplete(opts.provider),
      worker,
      role: `agent_task_${opts.phase}`,
      model: opts.config.model ?? opts.model,
      systemPrompt: buildSystemPrompt(opts),
      initialUserMessage: buildInitialMessage(opts),
      maxTurns: opts.config.maxTurns,
      maxTokensPerTurn: opts.config.maxTokensPerTurn,
      temperature: opts.config.temperature,
    });

    const result = await session.run();
    return {
      phase: opts.phase,
      backend: "secure_exec",
      content: result.content,
      turnsUsed: result.turnsUsed,
      executionHistory: result.executionHistory,
      error: null,
    };
  } catch (error) {
    return {
      phase: opts.phase,
      backend: "secure_exec",
      content: "",
      turnsUsed: 0,
      executionHistory: [],
      error: error instanceof Error ? error.message : String(error),
    };
  } finally {
    await worker.dispose();
  }
}
