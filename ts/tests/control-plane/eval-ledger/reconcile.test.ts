import { describe, expect, test } from "vitest";
import { reconcileEvalTrials } from "../../../src/control-plane/eval-ledger/index.js";
import type { EvalTrial } from "../../../src/control-plane/contract/types.js";

const trials: EvalTrial[] = [
  {
    taskId: "task-a",
    trialId: "task-a-1",
    attempt: 1,
    status: "passed",
    reward: 1,
  },
  {
    taskId: "task-b",
    trialId: "task-b-1",
    attempt: 1,
    status: "infrastructure-error",
    errorKind: "image-pull",
  },
  {
    taskId: "task-b",
    trialId: "task-b-2",
    attempt: 2,
    status: "failed",
    reward: 0,
    replacementForTrialId: "task-b-1",
  },
  {
    taskId: "task-c",
    trialId: "task-c-1",
    attempt: 1,
    status: "failed",
    reward: 0,
  },
  {
    taskId: "task-c",
    trialId: "task-c-2",
    attempt: 2,
    status: "passed",
    reward: 1,
  },
  {
    taskId: "task-d",
    trialId: "task-d-1",
    attempt: 1,
    status: "cancelled",
    errorKind: "manual-stop",
  },
];

describe("reconcileEvalTrials", () => {
  test("uses replacement trials for infrastructure failures without best-of-k leakage", () => {
    const reconciliation = reconcileEvalTrials(trials, {
      view: "first-completed-per-task",
    });

    expect(reconciliation.view).toBe("first-completed-per-task");
    expect(reconciliation.selectedTrialIdsByTask).toEqual({
      "task-a": "task-a-1",
      "task-b": "task-b-2",
      "task-c": "task-c-1",
    });
    expect(reconciliation.ignoredTrialIds).toEqual(["task-c-2"]);
    expect(reconciliation.unresolvedTaskIds).toEqual(["task-d"]);
    expect(reconciliation.counts).toMatchObject({
      taskCount: 4,
      selectedTaskCount: 3,
      passed: 1,
      failed: 2,
      infrastructureErrors: 1,
      cancelled: 1,
      discarded: 0,
      duplicatesIgnored: 1,
    });
    expect(reconciliation.score).toBeCloseTo(1 / 3);
  });

  test("can explicitly report best-of-k separately from the headline first-trial view", () => {
    const reconciliation = reconcileEvalTrials(trials, {
      view: "best-of-k",
    });

    expect(reconciliation.selectedTrialIdsByTask).toEqual({
      "task-a": "task-a-1",
      "task-b": "task-b-2",
      "task-c": "task-c-2",
    });
    expect(reconciliation.counts.passed).toBe(2);
    expect(reconciliation.counts.failed).toBe(1);
    expect(reconciliation.score).toBeCloseTo(2 / 3);
  });
});
