import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { getConceptModel } from "../src/concepts/model.js";

describe("concept model parity", () => {
  it("matches the shared machine-readable concept model", () => {
    const sharedModel = JSON.parse(
      readFileSync(
        join(import.meta.dirname, "..", "..", "docs", "concept-model.json"),
        "utf-8",
      ),
    );

    expect(getConceptModel()).toEqual(sharedModel);
  });
});
