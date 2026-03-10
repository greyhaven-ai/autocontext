import { describe, it, expect, vi } from "vitest";
import {
  ActionFilterHarness,
  ActionDictSchema,
} from "../src/execution/action-filter.js";
import type {
  ActionDict,
  ScenarioLike,
  HarnessLoaderLike,
} from "../src/execution/action-filter.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function mockScenario(
  actions: ActionDict[] | null = [
    { action: "move_up", description: "Move one cell up" },
    { action: "move_down", description: "Move one cell down" },
    { action: "capture_flag", description: "Capture the opponent flag", row: 1, col: 5 },
  ],
): ScenarioLike {
  return {
    enumerateLegalActions: vi.fn().mockReturnValue(actions),
    validateActions: vi.fn().mockImplementation(
      (_state: Record<string, unknown>, _pid: string, acts: Record<string, unknown>) => {
        if (acts.action === "move_up" || acts.action === "move_down") {
          return [true, "ok"] as [boolean, string];
        }
        return [false, "invalid action"] as [boolean, string];
      },
    ),
  };
}

function noEnumerateScenario(): ScenarioLike {
  return {
    enumerateLegalActions: vi.fn().mockReturnValue(null),
    validateActions: vi.fn().mockReturnValue([true, "ok"]),
  };
}

// ---------------------------------------------------------------------------
// getLegalActions
// ---------------------------------------------------------------------------

describe("ActionFilterHarness — getLegalActions", () => {
  it("returns scenario actions", () => {
    const h = new ActionFilterHarness(mockScenario());
    const actions = h.getLegalActions({});
    expect(actions).not.toBeNull();
    expect(actions).toHaveLength(3);
  });

  it("returns empty array for terminal state", () => {
    const h = new ActionFilterHarness(mockScenario([]));
    expect(h.getLegalActions({ terminal: true })).toEqual([]);
  });

  it("returns null when not supported", () => {
    const h = new ActionFilterHarness(noEnumerateScenario());
    expect(h.getLegalActions({})).toBeNull();
  });

  it("falls back to harness loader", () => {
    const loader: HarnessLoaderLike = {
      validators: [
        { enumerate_legal_actions: vi.fn().mockReturnValue([{ action: "from_harness", description: "harness" }]) },
      ],
    };
    const h = new ActionFilterHarness(noEnumerateScenario(), loader);
    const result = h.getLegalActions({});
    expect(result).not.toBeNull();
    expect(result![0].action).toBe("from_harness");
  });

  it("prefers scenario over harness", () => {
    const loader: HarnessLoaderLike = {
      validators: [
        { enumerate_legal_actions: vi.fn().mockReturnValue([{ action: "harness", description: "x" }]) },
      ],
    };
    const h = new ActionFilterHarness(mockScenario(), loader);
    const result = h.getLegalActions({});
    expect(result![0].action).toBe("move_up");
  });

  it("returns null when harness throws", () => {
    const loader: HarnessLoaderLike = {
      validators: [
        {
          enumerate_legal_actions: vi.fn().mockImplementation(() => {
            throw new Error("boom");
          }),
        },
      ],
    };
    const h = new ActionFilterHarness(noEnumerateScenario(), loader);
    expect(h.getLegalActions({})).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// formatActionPrompt
// ---------------------------------------------------------------------------

describe("ActionFilterHarness — formatActionPrompt", () => {
  it("creates numbered list", () => {
    const h = new ActionFilterHarness(mockScenario());
    const actions = h.getLegalActions({})!;
    const prompt = h.formatActionPrompt(actions);
    expect(prompt).toContain("1. move_up");
    expect(prompt).toContain("2. move_down");
    expect(prompt).toContain("3. capture_flag");
    expect(prompt).toContain("Select an action by number:");
  });

  it("includes descriptions", () => {
    const h = new ActionFilterHarness(mockScenario());
    const prompt = h.formatActionPrompt(h.getLegalActions({})!);
    expect(prompt).toContain("Move one cell up");
  });

  it("includes row/col", () => {
    const h = new ActionFilterHarness(mockScenario());
    const prompt = h.formatActionPrompt(h.getLegalActions({})!);
    expect(prompt).toContain("row 1");
    expect(prompt).toContain("col 5");
  });

  it("formats continuous type", () => {
    const h = new ActionFilterHarness(mockScenario());
    const actions: ActionDict[] = [
      { action: "weight", description: "A weight", type: "continuous", range: [0.0, 1.0] },
    ];
    const prompt = h.formatActionPrompt(actions);
    expect(prompt).toContain("Provide a JSON object");
    expect(prompt).toContain('"weight":0.5');
  });

  it("handles empty actions", () => {
    const h = new ActionFilterHarness(mockScenario());
    expect(h.formatActionPrompt([])).toBe("No actions available.");
  });
});

// ---------------------------------------------------------------------------
// parseActionSelection
// ---------------------------------------------------------------------------

describe("ActionFilterHarness — parseActionSelection", () => {
  it("parses numeric index", () => {
    const h = new ActionFilterHarness(mockScenario());
    const actions = h.getLegalActions({})!;
    const result = h.parseActionSelection("1", actions);
    expect(result && "action" in result ? result.action : undefined).toBe("move_up");
  });

  it("parses numeric with text", () => {
    const h = new ActionFilterHarness(mockScenario());
    const actions = h.getLegalActions({})!;
    const result = h.parseActionSelection("I choose 2", actions);
    expect(result && "action" in result ? result.action : undefined).toBe("move_down");
  });

  it("parses numeric with whitespace", () => {
    const h = new ActionFilterHarness(mockScenario());
    const actions = h.getLegalActions({})!;
    const result = h.parseActionSelection("  3  ", actions);
    expect(result && "action" in result ? result.action : undefined).toBe("capture_flag");
  });

  it("returns null for out-of-range index", () => {
    const h = new ActionFilterHarness(mockScenario());
    const actions = h.getLegalActions({})!;
    expect(h.parseActionSelection("99", actions)).toBeNull();
  });

  it("matches action name", () => {
    const h = new ActionFilterHarness(mockScenario());
    const actions = h.getLegalActions({})!;
    const result = h.parseActionSelection("I want to move_down please", actions);
    expect(result && "action" in result ? result.action : undefined).toBe("move_down");
  });

  it("returns null for no match", () => {
    const h = new ActionFilterHarness(mockScenario());
    const actions = h.getLegalActions({})!;
    expect(h.parseActionSelection("something unrelated", actions)).toBeNull();
  });

  it("returns null for empty actions", () => {
    const h = new ActionFilterHarness(mockScenario());
    expect(h.parseActionSelection("1", [])).toBeNull();
  });

  it("returns null for empty response", () => {
    const h = new ActionFilterHarness(mockScenario());
    const actions = h.getLegalActions({})!;
    expect(h.parseActionSelection("", actions)).toBeNull();
  });

  it("parses continuous JSON selection", () => {
    const h = new ActionFilterHarness(mockScenario());
    const actions: ActionDict[] = [
      { action: "aggression", description: "x", type: "continuous", range: [0, 1] },
      { action: "defense", description: "y", type: "continuous", range: [0, 1] },
    ];
    const result = h.parseActionSelection('{"aggression":0.6,"defense":0.4}', actions);
    expect(result).toEqual({ aggression: 0.6, defense: 0.4 });
  });

  it("returns null when continuous JSON misses keys", () => {
    const h = new ActionFilterHarness(mockScenario());
    const actions: ActionDict[] = [
      { action: "aggression", description: "x", type: "continuous", range: [0, 1] },
      { action: "defense", description: "y", type: "continuous", range: [0, 1] },
    ];
    expect(h.parseActionSelection('{"aggression":0.6}', actions)).toBeNull();
  });

  it("returns null when continuous JSON is out of range", () => {
    const h = new ActionFilterHarness(mockScenario());
    const actions: ActionDict[] = [
      { action: "aggression", description: "x", type: "continuous", range: [0, 1] },
      { action: "defense", description: "y", type: "continuous", range: [0, 1] },
    ];
    expect(h.parseActionSelection('{"aggression":1.6,"defense":0.4}', actions)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// verifyAction
// ---------------------------------------------------------------------------

describe("ActionFilterHarness — verifyAction", () => {
  it("returns true for valid action", () => {
    const h = new ActionFilterHarness(mockScenario());
    const [ok, reason] = h.verifyAction({}, "player", { action: "move_up" });
    expect(ok).toBe(true);
    expect(reason).toBe("ok");
  });

  it("returns false for invalid action", () => {
    const h = new ActionFilterHarness(mockScenario());
    const [ok, reason] = h.verifyAction({}, "player", { action: "fly" });
    expect(ok).toBe(false);
    expect(reason).toContain("invalid");
  });

  it("feedback includes reason", () => {
    const h = new ActionFilterHarness(mockScenario());
    const feedback = h.getVerifyFeedback("bad move", {});
    expect(feedback).toContain("bad move");
    expect(feedback).toContain("Please try again.");
  });

  it("feedback includes legal actions", () => {
    const h = new ActionFilterHarness(mockScenario());
    const feedback = h.getVerifyFeedback("bad move", {});
    expect(feedback).toContain("move_up");
    expect(feedback).toContain("move_down");
  });

  it("feedback without enumeration", () => {
    const h = new ActionFilterHarness(noEnumerateScenario());
    const feedback = h.getVerifyFeedback("bad move", {});
    expect(feedback).toContain("bad move");
    expect(feedback).not.toContain("move_up");
  });
});

// ---------------------------------------------------------------------------
// Schema validation
// ---------------------------------------------------------------------------

describe("ActionDictSchema", () => {
  it("validates minimal action", () => {
    const result = ActionDictSchema.safeParse({ action: "move", description: "desc" });
    expect(result.success).toBe(true);
  });

  it("validates full action", () => {
    const result = ActionDictSchema.safeParse({
      action: "move",
      description: "desc",
      type: "continuous",
      range: [0, 1],
      row: 1,
      col: 2,
    });
    expect(result.success).toBe(true);
  });

  it("rejects missing action", () => {
    const result = ActionDictSchema.safeParse({ description: "desc" });
    expect(result.success).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

describe("ActionFilterHarness — export", () => {
  it("is importable from index", async () => {
    const mod = await import("../src/index.js");
    expect(mod.ActionFilterHarness).toBeDefined();
    expect(mod.ActionDictSchema).toBeDefined();
  });
});
