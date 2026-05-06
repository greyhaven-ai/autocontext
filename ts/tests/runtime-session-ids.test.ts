import { describe, expect, it } from "vitest";

import { runtimeSessionIdForRun } from "../src/session/runtime-session-ids.js";

describe("runtime session ids", () => {
  it("derives the persisted runtime-session id for an autoctx run", () => {
    expect(runtimeSessionIdForRun("run-123")).toBe("run:run-123:runtime");
  });
});
