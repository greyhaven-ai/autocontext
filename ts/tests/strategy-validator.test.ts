import { describe, it, expect, vi } from "vitest";
import {
  StrategyValidator,
  ValidationResultSchema,
} from "../src/execution/strategy-validator.js";
import type { ValidationResult, MatchResult, ExecuteMatchFn } from "../src/execution/strategy-validator.js";

// Helper: creates a mock executeMatch that resolves with the given result
function makeSuccessMatch(result: MatchResult): ExecuteMatchFn {
  return vi.fn().mockResolvedValue(result) as unknown as ExecuteMatchFn;
}

// Helper: creates a mock executeMatch that rejects with the given error
function makeFailingMatch(error: string): ExecuteMatchFn {
  return vi.fn().mockRejectedValue(new Error(error)) as unknown as ExecuteMatchFn;
}

describe("StrategyValidator — basic validation", () => {
  it("test_valid_json_strategy_passes", async () => {
    const executeMatch = makeSuccessMatch({ score: 1.0, summary: "All good" });
    const validator = new StrategyValidator({ executeMatch });
    const strategy = { move: "attack", priority: "high" };

    const result = await validator.validate(strategy);

    expect(result.passed).toBe(true);
    expect(result.errors).toEqual([]);
    expect(result.matchSummary).toBe("All good");
  });

  it("test_invalid_strategy_detected", async () => {
    const executeMatch = makeFailingMatch("Strategy key 'move' is not valid");
    const validator = new StrategyValidator({ executeMatch });
    const strategy = { move: "invalid_move" };

    const result = await validator.validate(strategy);

    expect(result.passed).toBe(false);
    expect(result.errors).toHaveLength(1);
    expect(result.errors[0]).toBe("Strategy key 'move' is not valid");
  });

  it("test_validation_errors_in_result", async () => {
    const executeMatch = makeSuccessMatch({
      score: 0,
      summary: "Validation failed",
      validationErrors: ["Missing required key: direction", "Invalid value for speed"],
    });
    const validator = new StrategyValidator({ executeMatch });
    const strategy = { speed: 9999 };

    const result = await validator.validate(strategy);

    expect(result.passed).toBe(false);
    expect(result.errors).toEqual(["Missing required key: direction", "Invalid value for speed"]);
    expect(result.matchSummary).toBe("Validation failed");
  });

  it("test_code_strategy_passthrough", async () => {
    const executeMatch = vi.fn() as unknown as ExecuteMatchFn;
    const validator = new StrategyValidator({ executeMatch });
    const codeStrategy = { __code__: "def strategy(): return 'attack'" };

    const result = await validator.validate(codeStrategy);

    expect(result.passed).toBe(true);
    expect(result.errors).toEqual([]);
    expect(result.matchSummary).toBe("");
    // executeMatch must NOT have been called
    expect(executeMatch).not.toHaveBeenCalled();
  });
});

describe("StrategyValidator — formatRevisionPrompt", () => {
  it("test_format_revision_prompt_includes_errors", () => {
    const executeMatch = makeSuccessMatch({ score: 1.0, summary: "" });
    const validator = new StrategyValidator({ executeMatch });
    const result: ValidationResult = {
      passed: false,
      errors: ["Error one", "Error two"],
      matchSummary: "",
    };
    const strategy = { move: "attack" };

    const prompt = validator.formatRevisionPrompt(result, strategy);

    expect(prompt).toContain("Error one");
    expect(prompt).toContain("Error two");
    expect(prompt).toContain("1. Error one");
    expect(prompt).toContain("2. Error two");
  });

  it("test_format_revision_prompt_includes_strategy", () => {
    const executeMatch = makeSuccessMatch({ score: 1.0, summary: "" });
    const validator = new StrategyValidator({ executeMatch });
    const result: ValidationResult = {
      passed: false,
      errors: ["Some error"],
      matchSummary: "",
    };
    const strategy = { move: "attack", priority: "high" };

    const prompt = validator.formatRevisionPrompt(result, strategy);

    expect(prompt).toContain('"move": "attack"');
    expect(prompt).toContain('"priority": "high"');
    expect(prompt).toContain("```json");
    expect(prompt).toContain("```");
  });
});

describe("StrategyValidator — validateWithRetries", () => {
  it("test_validate_with_retries_passes_first_attempt", async () => {
    const executeMatch = makeSuccessMatch({ score: 1.0, summary: "OK" });
    const validator = new StrategyValidator({ executeMatch });
    const strategy = { move: "attack" };
    const revise = vi.fn();

    const { result, finalStrategy, attempts } = await validator.validateWithRetries(strategy, revise);

    expect(result.passed).toBe(true);
    expect(finalStrategy).toEqual(strategy);
    expect(attempts).toBe(1);
    expect(revise).not.toHaveBeenCalled();
  });

  it("test_validate_with_retries_succeeds_on_retry", async () => {
    let callCount = 0;
    const executeMatch = vi.fn().mockImplementation(async () => {
      callCount++;
      if (callCount === 1) {
        return { score: 0, summary: "Bad", validationErrors: ["Bad move"] };
      }
      return { score: 1.0, summary: "Fixed" };
    }) as unknown as ExecuteMatchFn;
    const validator = new StrategyValidator({ executeMatch, maxRetries: 2 });
    const strategy = { move: "bad" };
    const revisedStrategy = { move: "good" };
    const revise = vi.fn().mockResolvedValue(revisedStrategy);

    const { result, finalStrategy, attempts } = await validator.validateWithRetries(strategy, revise);

    expect(result.passed).toBe(true);
    expect(finalStrategy).toEqual(revisedStrategy);
    expect(attempts).toBe(2);
    expect(revise).toHaveBeenCalledOnce();
  });

  it("test_validate_with_retries_exhaustion", async () => {
    const executeMatch = makeSuccessMatch({
      score: 0,
      summary: "Always fails",
      validationErrors: ["Persistent error"],
    });
    const validator = new StrategyValidator({ executeMatch, maxRetries: 2 });
    const strategy = { move: "bad" };
    const revise = vi.fn().mockResolvedValue({ move: "still bad" });

    const { result, attempts } = await validator.validateWithRetries(strategy, revise);

    expect(result.passed).toBe(false);
    expect(attempts).toBe(3); // 1 initial + 2 retries
    expect(revise).toHaveBeenCalledTimes(2);
  });

  it("test_validate_with_retries_calls_revise", async () => {
    let callCount = 0;
    const executeMatch = vi.fn().mockImplementation(async () => {
      callCount++;
      if (callCount <= 1) {
        return { score: 0, summary: "Bad", validationErrors: ["Need fix"] };
      }
      return { score: 1.0, summary: "OK" };
    }) as unknown as ExecuteMatchFn;
    const validator = new StrategyValidator({ executeMatch, maxRetries: 2 });
    const strategy = { move: "bad" };
    const revise = vi.fn().mockResolvedValue({ move: "good" });

    await validator.validateWithRetries(strategy, revise);

    expect(revise).toHaveBeenCalledOnce();
    // The prompt passed to revise must contain the error text
    const promptArg = revise.mock.calls[0][0] as string;
    expect(promptArg).toContain("Need fix");
    expect(promptArg).toContain("failed pre-validation");
  });
});

describe("StrategyValidator — schema and defaults", () => {
  it("test_validation_result_schema_parse", () => {
    const raw = { passed: true, errors: ["e1"], matchSummary: "ok" };
    const parsed = ValidationResultSchema.parse(raw);
    expect(parsed.passed).toBe(true);
    expect(parsed.errors).toEqual(["e1"]);
    expect(parsed.matchSummary).toBe("ok");
  });

  it("test_validation_result_schema_defaults", () => {
    // errors and matchSummary have defaults
    const parsed = ValidationResultSchema.parse({ passed: false });
    expect(parsed.errors).toEqual([]);
    expect(parsed.matchSummary).toBe("");
  });

  it("test_default_max_retries", async () => {
    const executeMatch = vi.fn().mockImplementation(async () => {
      return { score: 0, summary: "fail", validationErrors: ["err"] };
    }) as unknown as ExecuteMatchFn;
    // No maxRetries specified — should default to 2
    const validator = new StrategyValidator({ executeMatch });
    const revise = vi.fn().mockResolvedValue({ move: "retry" });

    const { attempts } = await validator.validateWithRetries({ move: "bad" }, revise);

    // Default maxRetries=2 → 1 initial + 2 retries = 3 attempts total
    expect(attempts).toBe(3);
    expect(revise).toHaveBeenCalledTimes(2);
  });
});
