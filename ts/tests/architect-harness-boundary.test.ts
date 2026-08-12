/**
 * AC-930: preserve architect proposals at the public parser boundary.
 *
 * Harness specs contain Python source that this engine cannot execute, but
 * `ArchitectOutput` is a public package contract. Both parsers retain validated
 * proposals as opaque data instead of silently discarding them. Tool proposals
 * match Python's structural validation while remaining syntax-permissive until
 * an execution or persistence boundary exists.
 */
import { describe, expect, it } from "vitest";

import { parseArchitectOutput } from "../src/agents/roles.js";
import { ARCHITECT_SCHEMA, parseArchitectConstrained } from "../src/agents/role-schemas.js";

const HARNESS_CODE = "def validate(run):\n    return run.score > 0.5\n";

describe("architect harness boundary", () => {
  it("preserves harness specs from the markdown path as opaque proposals", () => {
    const markdown = [
      "## Proposal",
      "```json",
      JSON.stringify({
        tools: [{ name: "t", description: "d", code: "print(1)" }],
      }),
      "```",
      "<!-- HARNESS_START -->",
      JSON.stringify({
        harness: [{ name: "h", description: "d", code: HARNESS_CODE }],
      }),
      "<!-- HARNESS_END -->",
    ].join("\n");

    const parsed = parseArchitectOutput(markdown);

    expect(parsed.toolSpecs).toHaveLength(1);
    expect(parsed.harnessSpecs).toEqual([
      { name: "h", description: "d", code: HARNESS_CODE },
    ]);
  });

  it("preserves harness specs from the constrained path", () => {
    const payload = JSON.stringify({
      tools: [{ name: "t", description: "d", code: "print(1)" }],
      harness: [{ name: "h", description: "d", code: HARNESS_CODE }],
      changelog_entry: "e",
      dag_changes: [],
      mutations: [],
      observed_bottlenecks: [],
      tuning_parameters: [],
      tuning_reasoning: "",
      impact_hypothesis: "",
    });

    const parsed = parseArchitectConstrained(payload);

    expect(parsed.toolSpecs).toHaveLength(1);
    expect(parsed.harnessSpecs).toEqual([
      { name: "h", description: "d", code: HARNESS_CODE },
    ]);
  });

  it("keeps the harness channel in the shared schema", () => {
    const properties = (ARCHITECT_SCHEMA.schema as { properties: Record<string, unknown> })
      .properties;
    expect(Object.keys(properties)).toContain("harness");
  });

  it("accepts tool code that does not parse as Python", () => {
    // Pins the decision recorded on parseArchitectToolSpecs. Python's parser is
    // equally permissive here; its ast.parse runs at the persistence boundary in
    // storage/artifacts.py, right before the source is written to a .py file.
    // TypeScript has no such boundary, so rejecting here would make this engine
    // stricter than Python rather than aligned with it.
    const markdown = [
      "```json",
      JSON.stringify({ tools: [{ name: "t", description: "d", code: "def (" }] }),
      "```",
    ].join("\n");

    expect(parseArchitectOutput(markdown).toolSpecs).toHaveLength(1);
  });

  it("filters malformed tool entries and returns canonical fields", () => {
    const markdown = [
      "```json",
      JSON.stringify({
        tools: [
          null,
          "not an object",
          { name: 7, description: "d", code: "print(1)" },
          { name: "missing_code", description: "d" },
          {
            name: "valid_tool",
            description: "d",
            code: "print(1)",
            ignored: "not part of Python's parsed contract",
          },
        ],
      }),
      "```",
    ].join("\n");

    expect(parseArchitectOutput(markdown).toolSpecs).toEqual([
      { name: "valid_tool", description: "d", code: "print(1)" },
    ]);
  });
});
