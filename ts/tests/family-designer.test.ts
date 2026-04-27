import { describe, expect, it, vi } from "vitest";

import {
  designFamilySpec,
  parseFamilyDesignerSpec,
  type FamilyDesignerDescriptor,
} from "../src/scenarios/family-designer.js";

type ExampleSpec = {
  count: number;
  title: string;
};

const EXAMPLE_DESCRIPTOR: FamilyDesignerDescriptor<ExampleSpec> = {
  family: "example",
  startDelimiter: "<!-- EXAMPLE_START -->",
  endDelimiter: "<!-- EXAMPLE_END -->",
  missingDelimiterLabel: "EXAMPLE_SPEC",
  parseRaw: (raw) => ({
    count: Number(raw.count),
    title: String(raw.title),
  }),
};

describe("family designer pipeline", () => {
  it("parses delimited JSON through the shared descriptor path", () => {
    const spec = parseFamilyDesignerSpec(
      [
        "Here is the scenario:",
        "<!-- EXAMPLE_START -->",
        JSON.stringify({ count: "3", title: "Delimited" }),
        "<!-- EXAMPLE_END -->",
      ].join("\n"),
      EXAMPLE_DESCRIPTOR,
    );

    expect(spec).toEqual({ count: 3, title: "Delimited" });
  });

  it("falls back to raw JSON when delimiters are absent", () => {
    const spec = parseFamilyDesignerSpec(
      JSON.stringify({ count: "5", title: "Raw JSON" }),
      EXAMPLE_DESCRIPTOR,
    );

    expect(spec).toEqual({ count: 5, title: "Raw JSON" });
  });

  it("passes designer prompts through the shared design workflow", async () => {
    const llmFn = vi.fn(async () => JSON.stringify({ count: 8, title: "Designed" }));

    await expect(
      designFamilySpec(
        "make an example",
        "system prompt",
        EXAMPLE_DESCRIPTOR,
        llmFn,
      ),
    ).resolves.toEqual({ count: 8, title: "Designed" });

    expect(llmFn).toHaveBeenCalledWith(
      "system prompt",
      "User description:\nmake an example",
    );
  });
});
