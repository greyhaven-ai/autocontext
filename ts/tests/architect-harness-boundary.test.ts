/**
 * AC-930: the TypeScript engine drops the architect's harness channel on purpose.
 *
 * Both parsers hardcode `harnessSpecs: []`. Read cold that looks like an
 * unfinished port, and the previous fix for "looks unfinished" is what this
 * suite exists to prevent: someone wires the channel through, and the loop
 * starts handing callers Python source that this engine cannot execute.
 *
 * The boundary is real. Harness specs are Python validator source run by the
 * Python harness, and no consumer of harness specs exists anywhere in ts/src.
 * A comment alone rots; these assert the drop so restoring it is a deliberate,
 * visible act rather than an edit that quietly passes.
 */
import { describe, expect, it } from "vitest";

import { parseArchitectOutput } from "../src/agents/roles.js";
import { ARCHITECT_SCHEMA, parseArchitectConstrained } from "../src/agents/role-schemas.js";

const HARNESS_CODE = "def validate(run):\n    return run.score > 0.5\n";

describe("architect harness boundary", () => {
  it("drops harness specs the markdown path could have parsed", () => {
    const markdown = [
      "## Proposal",
      "```json",
      JSON.stringify({
        tools: [{ name: "t", description: "d", code: "print(1)" }],
        harness: [{ name: "h", description: "d", code: HARNESS_CODE }],
      }),
      "```",
    ].join("\n");

    const parsed = parseArchitectOutput(markdown);

    expect(parsed.toolSpecs).toHaveLength(1);
    expect(parsed.harnessSpecs).toEqual([]);
  });

  it("drops harness specs the constrained path definitely received", () => {
    // The sharper case. ARCHITECT_SCHEMA declares `harness`, so this payload is
    // schema-valid and the specs genuinely arrive before being discarded --
    // this is a drop, not an absence.
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
    expect(parsed.harnessSpecs).toEqual([]);
  });

  it("keeps the harness channel in the shared schema", () => {
    // Guards the claim the comments make. If the channel were ever removed from
    // the contract, "we deliberately drop a channel that exists" would silently
    // become a statement about nothing, and the comments would mislead.
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
});
