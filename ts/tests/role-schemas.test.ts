/**
 * AC-929: TypeScript constrained decoding, and the parity it restores.
 *
 * The schemas are read from docs/role-output-schemas.json, generated from the
 * pydantic models, so these tests also guard the property that makes that
 * indirection worth having: the two engines validate the SAME contract.
 */
import { describe, expect, it } from "vitest";

import { AgentOrchestrator } from "../src/agents/orchestrator.js";
import {
  ANALYST_SCHEMA,
  ARCHITECT_SCHEMA,
  COACH_SCHEMA,
  RoleOutputValidationError,
  parseAnalystConstrained,
  parseCoachConstrained,
  wasConstrained,
} from "../src/agents/role-schemas.js";
import { parseAnalystOutput } from "../src/agents/roles.js";
import type { CompletionResult, LLMProvider, OutputSchema } from "../src/types/index.js";

/** Records what the orchestrator sent, and can pretend to enforce it. */
class RecordingProvider implements LLMProvider {
  readonly name = "recording";
  readonly calls: Array<{ outputSchema?: OutputSchema }> = [];
  #enforce: boolean;
  #text: string;

  constructor(enforce: boolean, text: string) {
    this.#enforce = enforce;
    this.#text = text;
  }

  async complete(opts: {
    systemPrompt: string;
    userPrompt: string;
    outputSchema?: OutputSchema;
  }): Promise<CompletionResult> {
    this.calls.push({ outputSchema: opts.outputSchema });
    return { text: this.#text, usage: {}, constrained: this.#enforce };
  }

  defaultModel() {
    return "stub";
  }
}

const VALID_ANALYST = JSON.stringify({
  findings: ["reached the first flag in 6 steps"],
  root_causes: ["no tiebreak between equidistant flags"],
  recommendations: ["add a deterministic tiebreak on flag id"],
});

const DRIFTED = "### Findings\n\n* the agent oscillated between two flags";

describe("role output schemas", () => {
  it("rejects the drift the markdown scraper loses silently", () => {
    // Same defect as Python's: correct analysis, wrong heading level and
    // bullet marker, so every section comes back empty with nothing raised.
    const scraped = parseAnalystOutput(DRIFTED);
    expect(scraped.findings).toEqual([]);
    expect(scraped.parseSuccess).toBe(true); // reports success while empty

    expect(() => parseAnalystConstrained(DRIFTED)).toThrow(RoleOutputValidationError);
  });

  it("rejects an empty section rather than accepting it", () => {
    // minItems in the shared artifact is what makes this fail. Without it a
    // schema-valid payload could still carry an empty findings array, which is
    // the exact failure this work removes.
    expect(() =>
      parseAnalystConstrained(
        JSON.stringify({ findings: [], root_causes: ["r"], recommendations: ["x"] }),
      ),
    ).toThrow(RoleOutputValidationError);
  });

  it("rejects a missing field rather than defaulting it", () => {
    expect(() =>
      parseAnalystConstrained(JSON.stringify({ findings: ["f"], root_causes: ["r"] })),
    ).toThrow(RoleOutputValidationError);
  });

  it("carries the role, reason and offending text on the error", () => {
    try {
      parseAnalystConstrained(DRIFTED);
      expect.unreachable("should have thrown");
    } catch (err) {
      const e = err as RoleOutputValidationError;
      expect(e.role).toBe("analyst");
      expect(e.reason).toBeTruthy();
      expect(e.rawText).toBe(DRIFTED);
    }
  });

  it("renders markdown that round-trips through the scraper it replaces", () => {
    const out = parseAnalystConstrained(VALID_ANALYST);
    expect(parseAnalystOutput(out.rawMarkdown).findings).toEqual(out.findings);
    expect(parseAnalystOutput(out.rawMarkdown).rootCauses).toEqual(out.rootCauses);
  });

  it("round-trips coach output through the marker parser", () => {
    const out = parseCoachConstrained(JSON.stringify({ playbook: "P", lessons: "L", hints: "H" }));
    expect(out.rawMarkdown).toContain("<!-- PLAYBOOK_START -->");
    expect(out.playbook).toBe("P");
  });

  it("every schema is strict and complete", () => {
    // Derived, not hardcoded: every declared property must be required, which
    // is the invariant that stops a backend satisfying the schema while
    // omitting the fields the role exists to produce. A hardcoded list would
    // also drift the moment the pydantic models gain a channel -- which has
    // already happened once to architect.
    for (const schema of [ANALYST_SCHEMA, COACH_SCHEMA, ARCHITECT_SCHEMA]) {
      const body = schema.schema as {
        additionalProperties?: boolean;
        required?: string[];
        properties?: Record<string, unknown>;
      };
      expect(body.additionalProperties, schema.name).toBe(false);
      expect([...(body.required ?? [])].sort(), schema.name).toEqual(
        Object.keys(body.properties ?? {}).sort(),
      );
    }
  });
});

describe("wasConstrained", () => {
  it("treats absent as unconstrained, not as a third state", () => {
    // The reason the helper exists: `constrained` is optional so external
    // LLMProvider implementations keep compiling, which makes `undefined` a
    // real value at every read site.
    expect(wasConstrained({})).toBe(false);
    expect(wasConstrained({ constrained: undefined })).toBe(false);
    expect(wasConstrained({ constrained: false })).toBe(false);
    expect(wasConstrained({ constrained: true })).toBe(true);
  });
});

describe("orchestrator wiring", () => {
  const prompts = {
    competitorPrompt: "c",
    analystPrompt: "a",
    coachPrompt: "co",
    architectPrompt: "ar",
  };

  it("sends each role's schema to its provider", async () => {
    // The AC-913 lesson: a capability nothing passes is a capability that never
    // runs. This asserts the schema actually leaves the orchestrator.
    const provider = new RecordingProvider(true, VALID_ANALYST);
    await new AgentOrchestrator(provider).runGeneration(prompts).catch(() => undefined);

    const names = provider.calls.map((c) => c.outputSchema?.name);
    expect(names).toContain("analyst_output");
    expect(names).toContain("coach_output");
    // architect deliberately absent until its nine-channel payload is ported;
    // asserted so the exclusion is a recorded decision rather than an omission.
    expect(names).not.toContain("architect_output");
  });

  it("AC-931: the escape hatch stops the schema reaching the provider", async () => {
    // Asserts what the provider RECEIVED, not what the orchestrator intended.
    const provider = new RecordingProvider(true, VALID_ANALYST);
    await new AgentOrchestrator(provider, { constrainedOutput: false })
      .runGeneration(prompts)
      .catch(() => undefined);

    expect(provider.calls.every((c) => c.outputSchema === undefined)).toBe(true);
  });

  it("AC-931: output still parses via markdown when the hatch is open", async () => {
    const markdown =
      "## Findings\n\n- a\n\n## Root Causes\n\n- b\n\n## Actionable Recommendations\n\n- c";
    const result = await new AgentOrchestrator(new RecordingProvider(false, markdown), {
      constrainedOutput: false,
    }).runGeneration(prompts);
    expect(result.analystOutput.findings).toEqual(["a"]);
  });

  it("falls back to scraping when the backend did not enforce", async () => {
    const markdown =
      "## Findings\n\n- from markdown\n\n## Root Causes\n\n- rc\n\n## Actionable Recommendations\n\n- rec";
    const provider = new RecordingProvider(false, markdown);
    const result = await new AgentOrchestrator(provider).runGeneration(prompts);
    expect(result.analystOutput.findings).toEqual(["from markdown"]);
  });
});
