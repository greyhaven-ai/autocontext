/**
 * AC-526: CLI engine-result emitter contract.
 *
 * Every engine-driven CLI command (`simulate`, `investigate`, `analyze`,
 * `train`, compare/replay) must honor a single contract: when the engine
 * returns a failure-like status, the process exits non-zero — regardless
 * of whether output is JSON or human text. PR #628 patched `cmdSimulate`
 * only; this test suite drives out the shared `emitEngineResult` helper
 * that replaces all the duplicated blocks.
 *
 * These are pure unit tests with injected seams (no process spawn).
 */

import { describe, it, expect, vi } from "vitest";
import {
  emitEngineResult,
  isFailureStatus,
  type EngineResultLike,
} from "../src/cli/emit-engine-result.js";

/**
 * Build a set of injected fakes for the emitter so we can assert exactly
 * what it emitted and whether it tried to exit.
 */
function makeFakes() {
  const jsonCalls: unknown[] = [];
  const errorCalls: string[] = [];
  const exitCalls: number[] = [];
  return {
    writeJson: (payload: unknown) => {
      jsonCalls.push(payload);
    },
    writeError: (msg: string) => {
      errorCalls.push(msg);
    },
    // never-returning in production; for tests we record and continue so
    // we can assert post-conditions without the test process exiting.
    exitFn: ((code: number) => {
      exitCalls.push(code);
    }) as unknown as (code: number) => never,
    jsonCalls,
    errorCalls,
    exitCalls,
  };
}

describe("isFailureStatus", () => {
  it("treats 'failed' as failure", () => {
    expect(isFailureStatus("failed")).toBe(true);
  });

  it("treats 'error' as failure", () => {
    expect(isFailureStatus("error")).toBe(true);
  });

  it("treats 'incomplete' as failure (AC-527 alignment)", () => {
    // operator-loop contract violations mark runs as `incomplete`; the
    // CLI must surface those as non-zero so automation notices.
    expect(isFailureStatus("incomplete")).toBe(true);
  });

  it("treats 'completed' as success", () => {
    expect(isFailureStatus("completed")).toBe(false);
  });

  it("treats 'running' as success (non-terminal is not a failure)", () => {
    expect(isFailureStatus("running")).toBe(false);
  });

  it("is case-sensitive and rejects unknown states as success by default", () => {
    // Policy: unknown terminal states are treated as success to avoid
    // false alarms. Only explicitly failure-like states exit non-zero.
    expect(isFailureStatus("FAILED")).toBe(false);
    expect(isFailureStatus("done")).toBe(false);
  });
});

describe("emitEngineResult — JSON mode", () => {
  it("writes JSON and exits 1 when status is failed", () => {
    const fakes = makeFakes();
    const renderSuccess = vi.fn();
    const result: EngineResultLike & { extra: string } = {
      status: "failed",
      error: "fetch failed",
      extra: "payload",
    };

    emitEngineResult(result, {
      json: true,
      label: "Simulation",
      renderSuccess,
      exitFn: fakes.exitFn,
      writeJson: fakes.writeJson,
      writeError: fakes.writeError,
    });

    expect(fakes.jsonCalls).toHaveLength(1);
    expect(fakes.jsonCalls[0]).toEqual(result);
    expect(fakes.exitCalls).toEqual([1]);
    expect(fakes.errorCalls).toEqual([]);
    expect(renderSuccess).not.toHaveBeenCalled();
  });

  it("writes JSON and does not exit when status is completed", () => {
    const fakes = makeFakes();
    const renderSuccess = vi.fn();
    const result = { status: "completed", name: "deploy_sim" };

    emitEngineResult(result, {
      json: true,
      label: "Simulation",
      renderSuccess,
      exitFn: fakes.exitFn,
      writeJson: fakes.writeJson,
      writeError: fakes.writeError,
    });

    expect(fakes.jsonCalls).toHaveLength(1);
    expect(fakes.exitCalls).toEqual([]);
    expect(renderSuccess).not.toHaveBeenCalled(); // JSON mode never invokes text renderer
  });

  it("writes JSON and exits 1 when status is incomplete (contract violation)", () => {
    const fakes = makeFakes();
    const renderSuccess = vi.fn();
    const result = {
      status: "incomplete",
      missingSignals: ["escalation"],
      reason: "operator-loop contract violation",
    };

    emitEngineResult(result, {
      json: true,
      label: "Simulation",
      renderSuccess,
      exitFn: fakes.exitFn,
      writeJson: fakes.writeJson,
      writeError: fakes.writeError,
    });

    expect(fakes.exitCalls).toEqual([1]);
    expect(fakes.jsonCalls).toHaveLength(1);
  });

  it("writes JSON and exits 1 when status is error", () => {
    const fakes = makeFakes();
    const renderSuccess = vi.fn();
    const result = { status: "error", error: "unexpected" };

    emitEngineResult(result, {
      json: true,
      label: "Training",
      renderSuccess,
      exitFn: fakes.exitFn,
      writeJson: fakes.writeJson,
      writeError: fakes.writeError,
    });

    expect(fakes.exitCalls).toEqual([1]);
    expect(fakes.jsonCalls).toHaveLength(1);
  });
});

describe("emitEngineResult — text mode", () => {
  it("writes '<Label> failed: <error>' to stderr and exits 1 when status is failed", () => {
    const fakes = makeFakes();
    const renderSuccess = vi.fn();
    const result = { status: "failed", error: "fetch failed" };

    emitEngineResult(result, {
      json: false,
      label: "Simulation",
      renderSuccess,
      exitFn: fakes.exitFn,
      writeJson: fakes.writeJson,
      writeError: fakes.writeError,
    });

    expect(fakes.errorCalls).toEqual(["Simulation failed: fetch failed"]);
    expect(fakes.exitCalls).toEqual([1]);
    expect(fakes.jsonCalls).toEqual([]);
    expect(renderSuccess).not.toHaveBeenCalled();
  });

  it("writes '<Label> failed' (no colon, no error) when status is failed without an error message", () => {
    const fakes = makeFakes();
    const renderSuccess = vi.fn();
    const result = { status: "failed" };

    emitEngineResult(result, {
      json: false,
      label: "Investigation",
      renderSuccess,
      exitFn: fakes.exitFn,
      writeJson: fakes.writeJson,
      writeError: fakes.writeError,
    });

    expect(fakes.errorCalls).toEqual(["Investigation failed"]);
    expect(fakes.exitCalls).toEqual([1]);
  });

  it("writes '<Label> incomplete: <reason>' and exits 1 when status is incomplete", () => {
    const fakes = makeFakes();
    const renderSuccess = vi.fn();
    const result = {
      status: "incomplete",
      error: "missing required signals: escalation",
    };

    emitEngineResult(result, {
      json: false,
      label: "Simulation",
      renderSuccess,
      exitFn: fakes.exitFn,
      writeJson: fakes.writeJson,
      writeError: fakes.writeError,
    });

    expect(fakes.errorCalls).toEqual([
      "Simulation incomplete: missing required signals: escalation",
    ]);
    expect(fakes.exitCalls).toEqual([1]);
  });

  it("invokes renderSuccess and does not exit when status is completed", () => {
    const fakes = makeFakes();
    const result = { status: "completed", name: "deploy_sim" };
    const renderSuccess = vi.fn();

    emitEngineResult(result, {
      json: false,
      label: "Simulation",
      renderSuccess,
      exitFn: fakes.exitFn,
      writeJson: fakes.writeJson,
      writeError: fakes.writeError,
    });

    expect(renderSuccess).toHaveBeenCalledTimes(1);
    expect(renderSuccess).toHaveBeenCalledWith(result);
    expect(fakes.exitCalls).toEqual([]);
    expect(fakes.errorCalls).toEqual([]);
    expect(fakes.jsonCalls).toEqual([]);
  });

  it("does not call renderSuccess in any failure branch", () => {
    const renderSuccess = vi.fn();
    for (const status of ["failed", "error", "incomplete"]) {
      const fakes = makeFakes();
      emitEngineResult(
        { status, error: "problem" },
        {
          json: false,
          label: "Training",
          renderSuccess,
          exitFn: fakes.exitFn,
          writeJson: fakes.writeJson,
          writeError: fakes.writeError,
        },
      );
    }
    expect(renderSuccess).not.toHaveBeenCalled();
  });
});

describe("emitEngineResult — label-driven messaging (DRY)", () => {
  it("uses the provided label verbatim for every command", () => {
    const labels = [
      "Simulation",
      "Investigation",
      "Analysis",
      "Training",
      "Compare",
      "Replay",
    ];
    for (const label of labels) {
      const fakes = makeFakes();
      emitEngineResult(
        { status: "failed", error: "boom" },
        {
          json: false,
          label,
          renderSuccess: () => undefined,
          exitFn: fakes.exitFn,
          writeJson: fakes.writeJson,
          writeError: fakes.writeError,
        },
      );
      expect(fakes.errorCalls).toEqual([`${label} failed: boom`]);
    }
  });
});
