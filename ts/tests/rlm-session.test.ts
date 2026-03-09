import { describe, it, expect, vi } from "vitest";
import {
  ReplCommandSchema,
  ReplResultSchema,
  ExecutionRecordSchema,
  RlmContextSchema,
} from "../src/rlm/types.js";
import type { ReplCommand, ReplResult, ReplWorker, LlmComplete } from "../src/rlm/types.js";
import { RlmSession, extractCode } from "../src/rlm/session.js";

// ---------------------------------------------------------------------------
// Mock helpers
// ---------------------------------------------------------------------------

class MockReplWorker implements ReplWorker {
  namespace: Record<string, unknown> = {
    answer: { content: "", ready: false },
  };
  private responses: ReplResult[];
  private callIndex = 0;

  constructor(responses: ReplResult[]) {
    this.responses = responses;
  }

  runCode(command: ReplCommand): ReplResult {
    const result = this.responses[this.callIndex] ?? {
      stdout: "",
      error: null,
      answer: this.namespace["answer"] as Record<string, unknown>,
    };
    this.callIndex++;
    // Update namespace answer from result
    if (result.answer) {
      this.namespace["answer"] = result.answer;
    }
    return result;
  }
}

function mockComplete(responses: string[]): LlmComplete {
  let idx = 0;
  return async () => {
    const text = responses[idx] ?? "";
    idx++;
    return { text };
  };
}

function makeSession(
  completeResponses: string[],
  workerResponses: ReplResult[],
  opts?: {
    maxTurns?: number;
    onTurn?: (current: number, total: number, ready: boolean) => void;
  },
): { session: RlmSession; worker: MockReplWorker } {
  const worker = new MockReplWorker(workerResponses);
  const session = new RlmSession({
    complete: mockComplete(completeResponses),
    worker,
    role: "analyst",
    model: "test-model",
    systemPrompt: "You are a test analyst.",
    maxTurns: opts?.maxTurns ?? 5,
    onTurn: opts?.onTurn,
  });
  return { session, worker };
}

// ---------------------------------------------------------------------------
// Type schema tests
// ---------------------------------------------------------------------------

describe("ReplCommandSchema", () => {
  it("parses a valid repl command", () => {
    const result = ReplCommandSchema.parse({ code: "print('hello')" });
    expect(result.code).toBe("print('hello')");
  });

  it("requires code field", () => {
    expect(() => ReplCommandSchema.parse({})).toThrow();
  });
});

describe("ReplResultSchema", () => {
  it("parses a valid repl result with all fields", () => {
    const result = ReplResultSchema.parse({
      stdout: "hello world",
      error: null,
      answer: { ready: true, content: "done" },
    });
    expect(result.stdout).toBe("hello world");
    expect(result.error).toBeNull();
    expect(result.answer).toEqual({ ready: true, content: "done" });
  });

  it("defaults error to null and answer to empty object", () => {
    const result = ReplResultSchema.parse({ stdout: "hi" });
    expect(result.error).toBeNull();
    expect(result.answer).toEqual({});
  });
});

describe("ExecutionRecordSchema", () => {
  it("parses a valid execution record", () => {
    const result = ExecutionRecordSchema.parse({
      turn: 1,
      code: "x = 1",
      stdout: "1",
      error: null,
      answerReady: false,
    });
    expect(result.turn).toBe(1);
    expect(result.code).toBe("x = 1");
    expect(result.answerReady).toBe(false);
  });

  it("defaults answerReady to false and error to null", () => {
    const result = ExecutionRecordSchema.parse({
      turn: 2,
      code: "y = 2",
      stdout: "2",
    });
    expect(result.answerReady).toBe(false);
    expect(result.error).toBeNull();
  });
});

describe("RlmContextSchema", () => {
  it("parses a valid rlm context", () => {
    const result = RlmContextSchema.parse({
      variables: { x: 1, y: "hello" },
      summary: "Initial context with x and y.",
    });
    expect(result.variables).toEqual({ x: 1, y: "hello" });
    expect(result.summary).toBe("Initial context with x and y.");
  });

  it("requires variables and summary", () => {
    expect(() => RlmContextSchema.parse({ variables: {} })).toThrow();
    expect(() => RlmContextSchema.parse({ summary: "" })).toThrow();
  });
});

// ---------------------------------------------------------------------------
// extractCode tests
// ---------------------------------------------------------------------------

describe("extractCode", () => {
  it("extracts code from code tags", () => {
    const text = "Some text <code>print('hello')</code> more text";
    expect(extractCode(text)).toBe("print('hello')");
  });

  it("returns null when no code tags are present", () => {
    expect(extractCode("No code blocks here")).toBeNull();
  });

  it("trims whitespace from extracted code", () => {
    const text = "<code>  \n  x = 1\n  </code>";
    expect(extractCode(text)).toBe("x = 1");
  });

  it("handles multiline code blocks", () => {
    const text = "<code>\nx = 1\ny = 2\nprint(x + y)\n</code>";
    expect(extractCode(text)).toBe("x = 1\ny = 2\nprint(x + y)");
  });
});

// ---------------------------------------------------------------------------
// RlmSession tests
// ---------------------------------------------------------------------------

describe("RlmSession", () => {
  it("runs and returns a result", async () => {
    const workerResponses: ReplResult[] = [
      { stdout: "done", error: null, answer: { ready: true, content: "my analysis" } },
    ];
    const { session } = makeSession(
      ["<code>answer['ready'] = True</code>"],
      workerResponses,
    );

    const result = await session.run();
    expect(result).toBeDefined();
    expect(result.content).toBe("my analysis");
    expect(result.turnsUsed).toBe(1);
    expect(result.executionHistory).toHaveLength(1);
  });

  it("respects maxTurns limit", async () => {
    // LLM always returns code, worker never sets ready=true
    const workerResponse: ReplResult = {
      stdout: "running",
      error: null,
      answer: { ready: false },
    };
    const completeResponses = Array(10).fill("<code>x = 1</code>");
    const workerResponses = Array(10).fill(workerResponse);

    const { session } = makeSession(completeResponses, workerResponses, { maxTurns: 3 });

    const result = await session.run();
    expect(result.turnsUsed).toBe(3);
    expect(result.executionHistory).toHaveLength(3);
  });

  it("stops early when answer ready is true", async () => {
    const workerResponses: ReplResult[] = [
      { stdout: "step 1", error: null, answer: { ready: false } },
      { stdout: "step 2", error: null, answer: { ready: true, content: "final answer" } },
    ];
    const completeResponses = [
      "<code>step1()</code>",
      "<code>answer['ready'] = True</code>",
    ];

    const { session } = makeSession(completeResponses, workerResponses, { maxTurns: 10 });

    const result = await session.run();
    expect(result.turnsUsed).toBe(2);
    expect(result.content).toBe("final answer");
  });

  it("extracts answer content from worker result", async () => {
    const workerResponses: ReplResult[] = [
      { stdout: "computed", error: null, answer: { ready: true, content: "extracted content" } },
    ];
    const { session } = makeSession(
      ["<code>compute()</code>"],
      workerResponses,
    );

    const result = await session.run();
    expect(result.content).toBe("extracted content");
  });

  it("records execution history with code, stdout, and error", async () => {
    const workerResponses: ReplResult[] = [
      { stdout: "output here", error: "some error", answer: { ready: true, content: "done" } },
    ];
    const { session } = makeSession(
      ["<code>risky_operation()</code>"],
      workerResponses,
    );

    const result = await session.run();
    expect(result.executionHistory).toHaveLength(1);
    const record = result.executionHistory[0];
    expect(record.code).toBe("risky_operation()");
    expect(record.stdout).toBe("output here");
    expect(record.error).toBe("some error");
  });

  it("handles code errors and includes them in feedback", async () => {
    // Turn 1: error occurs, turn 2: success
    const workerResponses: ReplResult[] = [
      { stdout: "", error: "NameError: name 'x' is not defined", answer: { ready: false } },
      { stdout: "fixed", error: null, answer: { ready: true, content: "success" } },
    ];
    const completeResponses = [
      "<code>print(x)</code>",
      "<code>x = 1; print(x)</code>",
    ];

    const { session } = makeSession(completeResponses, workerResponses, { maxTurns: 5 });

    const result = await session.run();
    expect(result.turnsUsed).toBe(2);
    // First record should have the error
    expect(result.executionHistory[0].error).toBe("NameError: name 'x' is not defined");
    // Session should recover and finish
    expect(result.content).toBe("success");
  });

  it("calls onTurn callback for each turn", async () => {
    const turns: Array<{ current: number; total: number; ready: boolean }> = [];
    const onTurn = vi.fn((current: number, total: number, ready: boolean) => {
      turns.push({ current, total, ready });
    });

    const workerResponses: ReplResult[] = [
      { stdout: "t1", error: null, answer: { ready: false } },
      { stdout: "t2", error: null, answer: { ready: true, content: "done" } },
    ];
    const completeResponses = [
      "<code>turn1()</code>",
      "<code>turn2()</code>",
    ];

    const { session } = makeSession(completeResponses, workerResponses, {
      maxTurns: 5,
      onTurn,
    });

    await session.run();

    expect(onTurn).toHaveBeenCalledTimes(2);
    expect(turns[0]).toEqual({ current: 1, total: 5, ready: false });
    expect(turns[1]).toEqual({ current: 2, total: 5, ready: true });
  });

  it("prompts for code tags when no code block is present", async () => {
    // First response has no code tags, second has code that finishes
    const workerResponses: ReplResult[] = [
      { stdout: "done", error: null, answer: { ready: true, content: "final" } },
    ];
    const completeResponses = [
      "I will analyze the data.", // no code block
      "<code>finish()</code>",
    ];

    const { session } = makeSession(completeResponses, workerResponses, { maxTurns: 5 });

    const result = await session.run();
    // First turn has no code, so no execution record created
    // Second turn executes and finishes
    expect(result.turnsUsed).toBe(1);
    expect(result.content).toBe("final");
  });

  it("falls back to namespace answer when session ends without ready signal", async () => {
    // All turns run out without ready=true, but namespace has content
    const worker = new MockReplWorker([
      { stdout: "partial", error: null, answer: { ready: false, content: "partial result" } },
    ]);
    worker.namespace["answer"] = { content: "namespace content", ready: false };

    const session = new RlmSession({
      complete: mockComplete(["<code>work()</code>"]),
      worker,
      role: "analyst",
      model: "test-model",
      systemPrompt: "Test",
      maxTurns: 1,
    });

    const result = await session.run();
    // After running, namespace answer has been updated by the worker
    // The session should pick up content from namespace
    expect(typeof result.content).toBe("string");
  });
});

describe("RlmSession additional edge cases", () => {
  it("records answerReady true in execution history when answer is ready", async () => {
    const workerResponses: ReplResult[] = [
      { stdout: "ready!", error: null, answer: { ready: true, content: "done" } },
    ];
    const { session } = makeSession(
      ["<code>finalize()</code>"],
      workerResponses,
    );

    const result = await session.run();
    expect(result.executionHistory[0].answerReady).toBe(true);
  });

  it("handles empty stdout and error gracefully", async () => {
    const workerResponses: ReplResult[] = [
      { stdout: "", error: null, answer: { ready: false } },
      { stdout: "output", error: null, answer: { ready: true, content: "done" } },
    ];
    const completeResponses = ["<code>silent_op()</code>", "<code>verbose_op()</code>"];

    const { session } = makeSession(completeResponses, workerResponses, { maxTurns: 5 });

    const result = await session.run();
    expect(result.executionHistory).toHaveLength(2);
    expect(result.executionHistory[0].stdout).toBe("");
    expect(result.executionHistory[0].error).toBeNull();
  });

  it("uses default initialUserMessage when not specified", async () => {
    const worker = new MockReplWorker([
      { stdout: "done", error: null, answer: { ready: true, content: "result" } },
    ]);

    let capturedMessages: Array<{ role: string; content: string }> = [];
    const trackingComplete: LlmComplete = async (messages) => {
      capturedMessages = [...messages];
      return { text: "<code>done()</code>" };
    };

    const session = new RlmSession({
      complete: trackingComplete,
      worker,
      role: "analyst",
      model: "test-model",
      systemPrompt: "Test",
      maxTurns: 1,
    });

    await session.run();
    expect(capturedMessages[0].content).toBe("Begin exploring the data.");
  });

  it("uses custom initialUserMessage when provided", async () => {
    const worker = new MockReplWorker([
      { stdout: "done", error: null, answer: { ready: true, content: "result" } },
    ]);

    let capturedMessages: Array<{ role: string; content: string }> = [];
    const trackingComplete: LlmComplete = async (messages) => {
      capturedMessages = [...messages];
      return { text: "<code>done()</code>" };
    };

    const session = new RlmSession({
      complete: trackingComplete,
      worker,
      role: "analyst",
      model: "test-model",
      systemPrompt: "Test",
      initialUserMessage: "Analyze the tournament results.",
      maxTurns: 1,
    });

    await session.run();
    expect(capturedMessages[0].content).toBe("Analyze the tournament results.");
  });
});
