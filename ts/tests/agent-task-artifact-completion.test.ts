import { describe, expect, it, vi } from "vitest";

import { HookBus, HookEvents } from "../src/extensions/index.js";
import {
  AGENT_TASK_MAX_ACCUMULATED_CHARACTERS,
  AGENT_TASK_SEGMENT_MAX_TOKENS,
  completeAgentTaskArtifact,
} from "../src/scenarios/agent-task-artifact-completion.js";
import type { CompletionResult, LLMProvider } from "../src/types/index.js";

function providerWithResults(results: CompletionResult[]): LLMProvider & {
  complete: ReturnType<typeof vi.fn>;
} {
  return {
    name: "test-provider",
    defaultModel: () => "test-model",
    complete: vi.fn(async () => {
      const result = results.shift();
      if (!result) throw new Error("Unexpected provider call");
      return result;
    }),
  };
}

describe("agent-task artifact completion", () => {
  it("continues twice, removes boundary overlap, and aggregates metering", async () => {
    const provider = providerWithResults([
      {
        text: "Part one. café 🧪",
        model: "test-model",
        usage: { input: 2, output: 3 },
        costUsd: 0.01,
        stopReason: "max_tokens",
      },
      {
        text: "🧪 Part two.",
        model: "test-model",
        usage: { input: 5, output: 7 },
        costUsd: 0.02,
        stopReason: "length",
      },
      {
        text: "Part two. Part three.",
        model: "test-model",
        usage: { input: 11, output: 13 },
        costUsd: 0.03,
        stopReason: "end_turn",
        metadata: { providerSegment: 3 },
      },
    ]);
    const bus = new HookBus();
    const requests: Array<Record<string, unknown>> = [];
    const responses: Array<Record<string, unknown>> = [];
    bus.on(HookEvents.BEFORE_PROVIDER_REQUEST, (event) => {
      requests.push({ ...event.payload });
      return undefined;
    });
    bus.on(HookEvents.AFTER_PROVIDER_RESPONSE, (event) => {
      responses.push({ ...event.payload });
      return undefined;
    });

    const result = await completeAgentTaskArtifact({
      provider,
      hookBus: bus,
      role: "agent_task_initial",
      artifactLabel: "initial response",
      systemPrompt: "Create the artifact.",
      userPrompt: "Return all three parts.",
      metadata: { run_id: "run-1" },
    });

    expect(result.text).toBe("Part one. café 🧪 Part two. Part three.");
    expect(result.usage).toEqual({ input: 18, output: 23 });
    expect(result.costUsd).toBeCloseTo(0.06);
    expect(result.metadata).toEqual({
      providerSegment: 3,
      agentTaskContinuationCount: 2,
    });
    expect(provider.complete).toHaveBeenCalledTimes(3);
    expect(provider.complete.mock.calls.map(([request]) => request.maxTokens)).toEqual([
      AGENT_TASK_SEGMENT_MAX_TOKENS,
      AGENT_TASK_SEGMENT_MAX_TOKENS,
      AGENT_TASK_SEGMENT_MAX_TOKENS,
    ]);
    expect(provider.complete.mock.calls[1]?.[0].userPrompt).toContain(
      "<artifact_so_far>\nPart one. café 🧪\n</artifact_so_far>",
    );
    expect(requests).toHaveLength(3);
    expect(requests[0]).not.toHaveProperty("agent_task_continuation");
    expect(requests[1]).toMatchObject({
      role: "agent_task_initial",
      run_id: "run-1",
      agent_task_continuation: true,
      agent_task_continuation_segment: 1,
    });
    expect(requests[2]).toMatchObject({
      agent_task_continuation: true,
      agent_task_continuation_segment: 2,
    });
    expect(responses).toHaveLength(3);
    expect(responses[2]).toMatchObject({
      agent_task_continuation: true,
      agent_task_continuation_segment: 2,
    });
  });

  it("accepts a provider that replays the whole artifact plus new text", async () => {
    const provider = providerWithResults([
      { text: "Part one.", usage: {}, stopReason: "max_tokens" },
      { text: "Part one. Part two.", usage: {}, stopReason: "end_turn" },
    ]);

    const result = await completeAgentTaskArtifact({
      provider,
      role: "agent_task_revise",
      artifactLabel: "revision",
      systemPrompt: "Revise.",
      userPrompt: "Finish both parts.",
    });

    expect(result.text).toBe("Part one. Part two.");
    expect(result.metadata).toMatchObject({ agentTaskContinuationCount: 1 });
  });

  it.each([
    {
      label: "empty initial segment",
      results: [{ text: "   ", usage: {}, stopReason: "end_turn" }],
      error: /initial segment returned no usable text/i,
    },
    {
      label: "empty continuation",
      results: [
        { text: "Partial artifact.", usage: {}, stopReason: "max_tokens" },
        { text: "", usage: {}, stopReason: "end_turn" },
      ],
      error: /continuation 1 returned no new text/i,
    },
    {
      label: "non-progressing continuation",
      results: [
        { text: "Partial artifact.", usage: {}, stopReason: "max_tokens" },
        { text: "Partial artifact.\n", usage: {}, stopReason: "end_turn" },
      ],
      error: /continuation 1 returned no new text/i,
    },
  ])("fails closed on a $label", async ({ results, error }) => {
    const provider = providerWithResults(results);
    await expect(
      completeAgentTaskArtifact({
        provider,
        role: "agent_task_initial",
        artifactLabel: "initial response",
        systemPrompt: "Create.",
        userPrompt: "Finish.",
      }),
    ).rejects.toThrow(error);
  });

  it("fails closed after the bounded continuation limit", async () => {
    const provider = providerWithResults([
      { text: "Part one.", usage: {}, stopReason: "max_tokens" },
      { text: " Part two.", usage: {}, stopReason: "length" },
      { text: " Part three.", usage: {}, stopReason: "max_tokens" },
    ]);

    await expect(
      completeAgentTaskArtifact({
        provider,
        role: "agent_task_initial",
        artifactLabel: "initial response",
        systemPrompt: "Create.",
        userPrompt: "Finish.",
      }),
    ).rejects.toThrow(/remained truncated after 2 continuation attempts/i);
    expect(provider.complete).toHaveBeenCalledTimes(3);
  });

  it("fails closed when accumulated output exceeds the hard size bound", async () => {
    const provider = providerWithResults([
      {
        text: "x".repeat(AGENT_TASK_MAX_ACCUMULATED_CHARACTERS + 1),
        usage: {},
        stopReason: "end_turn",
      },
    ]);

    await expect(
      completeAgentTaskArtifact({
        provider,
        role: "agent_task_initial",
        artifactLabel: "initial response",
        systemPrompt: "Create.",
        userPrompt: "Finish.",
      }),
    ).rejects.toThrow(/exceeded the bounded accumulated output size/i);
  });
});
