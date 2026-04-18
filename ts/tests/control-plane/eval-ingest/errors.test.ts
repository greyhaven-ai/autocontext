import { describe, test, expect } from "vitest";
import { EvalRunAlreadyAttachedError } from "../../../src/control-plane/eval-ingest/errors.js";
import type { ArtifactId } from "../../../src/control-plane/contract/branded-ids.js";

describe("EvalRunAlreadyAttachedError", () => {
  test("carries artifactId and runId fields and is an Error", () => {
    const id = "01KPEYB3BRQWK2WSHK9E93N6NP" as ArtifactId;
    const err = new EvalRunAlreadyAttachedError(id, "run_1");
    expect(err).toBeInstanceOf(Error);
    expect(err).toBeInstanceOf(EvalRunAlreadyAttachedError);
    expect(err.artifactId).toBe(id);
    expect(err.runId).toBe("run_1");
    expect(err.name).toBe("EvalRunAlreadyAttachedError");
    expect(err.message).toContain("run_1");
    expect(err.message).toContain(id);
  });

  test("message is human-readable", () => {
    const err = new EvalRunAlreadyAttachedError(
      "01KPEYB3BRQWK2WSHK9E93N6NP" as ArtifactId,
      "run_xyz",
    );
    expect(err.message.toLowerCase()).toContain("already");
  });
});
