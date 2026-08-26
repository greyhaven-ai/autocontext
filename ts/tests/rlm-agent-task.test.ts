import { describe, it, expect, vi } from "vitest";
import type { LLMProvider } from "../src/types/index.js";
import { runAgentTaskRlmSession } from "../src/rlm/index.js";
import { SecureExecReplWorker } from "../src/rlm/secure-exec-worker.js";

function makeProvider(response: string): LLMProvider {
  return {
    name: "mock",
    defaultModel: () => "mock-model",
    complete: async () => ({
      text: response,
      model: "mock-model",
      usage: {},
    }),
  };
}

describe("runAgentTaskRlmSession", () => {
  it("runs a generate session and returns final content", async () => {
    const result = await runAgentTaskRlmSession({
      provider: makeProvider('<code>answer.ready = true;\nanswer.content = "RLM final answer";</code>'),
      model: "mock-model",
      config: {
        enabled: true,
        maxTurns: 2,
        maxTokensPerTurn: 512,
        temperature: 0.1,
        maxStdoutChars: 4096,
        codeTimeoutMs: 5000,
        memoryLimitMb: 64,
      },
      phase: "generate",
      taskPrompt: "Explain testing.",
      rubric: "Be clear.",
    });

    expect(result.error).toBeNull();
    expect(result.content).toBe("RLM final answer");
    expect(result.turnsUsed).toBe(1);
  });

  it("uses and closes a no-tools provider without invoking shared state", async () => {
    const sharedComplete = vi.fn(async () => ({ text: "must not run", usage: {} }));
    const isolatedClose = vi.fn();
    const isolatedComplete = vi.fn(async () => ({
      text: '<code>answer.ready = true; answer.content = "isolated answer";</code>',
      usage: {},
    }));
    const provider: LLMProvider = {
      name: "tool-capable-shared",
      defaultModel: () => "mock-model",
      complete: sharedComplete,
      createIsolatedProvider: (policy) => {
        expect(policy).toEqual({ noTools: true });
        return {
          name: "isolated-no-tools",
          defaultModel: () => "mock-model",
          complete: isolatedComplete,
          close: isolatedClose,
        };
      },
    };

    const result = await runAgentTaskRlmSession({
      provider,
      model: "mock-model",
      config: {
        enabled: true,
        maxTurns: 2,
        maxTokensPerTurn: 512,
        temperature: 0.1,
        maxStdoutChars: 4096,
        codeTimeoutMs: 5000,
        memoryLimitMb: 64,
      },
      phase: "generate",
      taskPrompt: "Private task",
      rubric: "Private rubric",
      requiresNoToolsProviderIsolation: true,
    });

    expect(result.content).toBe("isolated answer");
    expect(sharedComplete).not.toHaveBeenCalled();
    expect(isolatedComplete).toHaveBeenCalledOnce();
    expect(isolatedClose).toHaveBeenCalledOnce();
  });

  it("fails before any provider call when no-tools isolation is unavailable", async () => {
    const complete = vi.fn(async () => ({ text: "must not run", usage: {} }));
    await expect(
      runAgentTaskRlmSession({
        provider: {
          name: "tool-capable-shared",
          defaultModel: () => "mock-model",
          complete,
        },
        model: "mock-model",
        config: {
          enabled: true,
          maxTurns: 1,
          maxTokensPerTurn: 512,
          temperature: 0.1,
          maxStdoutChars: 4096,
          codeTimeoutMs: 5000,
          memoryLimitMb: 64,
        },
        phase: "generate",
        taskPrompt: "Private task",
        rubric: "Private rubric",
        requiresNoToolsProviderIsolation: true,
      }),
    ).rejects.toThrow(/cannot guarantee no-tools isolation/i);
    expect(complete).not.toHaveBeenCalled();
  });

  it("closes an owned no-tools provider when worker disposal rejects", async () => {
    const originalDispose = SecureExecReplWorker.prototype.dispose;
    const disposeSpy = vi
      .spyOn(SecureExecReplWorker.prototype, "dispose")
      .mockImplementationOnce(async function (this: SecureExecReplWorker) {
        await originalDispose.call(this);
        throw new Error("dispose failed");
      });
    const isolatedClose = vi.fn();
    const provider: LLMProvider = {
      name: "shared-tool-capable",
      defaultModel: () => "mock-model",
      complete: async () => ({ text: "must not run", usage: {} }),
      createIsolatedProvider: () => ({
        name: "isolated-no-tools",
        defaultModel: () => "mock-model",
        complete: async () => ({
          text: '<code>answer.ready = true; answer.content = "done";</code>',
          usage: {},
        }),
        close: isolatedClose,
      }),
    };

    try {
      await expect(runAgentTaskRlmSession({
        provider,
        model: "mock-model",
        config: {
          enabled: true,
          maxTurns: 1,
          maxTokensPerTurn: 512,
          temperature: 0.1,
          maxStdoutChars: 4096,
          codeTimeoutMs: 5000,
          memoryLimitMb: 64,
        },
        phase: "generate",
        taskPrompt: "Private task",
        rubric: "Private rubric",
        requiresNoToolsProviderIsolation: true,
      })).rejects.toThrow("dispose failed");
    } finally {
      disposeSpy.mockRestore();
    }
    expect(isolatedClose).toHaveBeenCalledOnce();
  });

  it("closes an owned provider before worker disposal settles", async () => {
    const originalDispose = SecureExecReplWorker.prototype.dispose;
    let markDisposeStarted: () => void = () => {};
    let releaseDispose: () => void = () => {};
    const disposeStarted = new Promise<void>((resolve) => {
      markDisposeStarted = resolve;
    });
    const disposeGate = new Promise<void>((resolve) => {
      releaseDispose = resolve;
    });
    const disposeSpy = vi
      .spyOn(SecureExecReplWorker.prototype, "dispose")
      .mockImplementationOnce(async function (this: SecureExecReplWorker) {
        markDisposeStarted();
        await disposeGate;
        await originalDispose.call(this);
      });
    const isolatedClose = vi.fn();
    const run = runAgentTaskRlmSession({
      provider: {
        name: "shared-tool-capable",
        defaultModel: () => "mock-model",
        complete: async () => ({ text: "must not run", usage: {} }),
        createIsolatedProvider: () => ({
          name: "isolated-no-tools",
          defaultModel: () => "mock-model",
          complete: async () => ({
            text: '<code>answer.ready = true; answer.content = "done";</code>',
            usage: {},
          }),
          close: isolatedClose,
        }),
      },
      model: "mock-model",
      config: {
        enabled: true,
        maxTurns: 1,
        maxTokensPerTurn: 512,
        temperature: 0.1,
        maxStdoutChars: 4096,
        codeTimeoutMs: 5000,
        memoryLimitMb: 64,
      },
      phase: "generate",
      taskPrompt: "Private task",
      rubric: "Private rubric",
      requiresNoToolsProviderIsolation: true,
    });

    await disposeStarted;
    try {
      expect(isolatedClose).toHaveBeenCalledOnce();
    } finally {
      releaseDispose();
      try {
        await run;
      } finally {
        disposeSpy.mockRestore();
      }
    }
  });
});
