import { describe, expect, it } from "vitest";

import {
  buildInvestigationConclusion,
  buildInvestigationEvidence,
  evaluateInvestigationHypotheses,
  identifyInvestigationUnknowns,
  recommendInvestigationNextSteps,
} from "../src/investigation/investigation-analysis-workflow.js";

describe("investigation analysis workflow", () => {
  it("builds evidence entries from collected evidence", () => {
    expect(buildInvestigationEvidence({
      collectedEvidence: [
        { id: "db", content: "Database saturation detected", isRedHerring: false },
        { id: "cache", content: "Cache warning", isRedHerring: true },
      ],
    })).toEqual([
      {
        id: "db",
        kind: "observation",
        source: "scenario execution",
        summary: "Database saturation detected",
        supports: [],
        contradicts: [],
        isRedHerring: false,
      },
      {
        id: "cache",
        kind: "red_herring",
        source: "scenario execution",
        summary: "Cache warning",
        supports: [],
        contradicts: [],
        isRedHerring: true,
      },
    ]);
  });

  it("annotates evidence support/contradiction and hypothesis status", () => {
    const evidence = buildInvestigationEvidence({
      collectedEvidence: [
        { id: "db", content: "Database saturation detected", isRedHerring: false },
        { id: "cache", content: "Cache warning on unrelated node", isRedHerring: true },
      ],
    });

    const result = evaluateInvestigationHypotheses(
      {
        hypotheses: [
          { statement: "Database saturation caused the outage", confidence: 0.8 },
          { statement: "Cache warning on unrelated node caused the outage", confidence: 0.6 },
        ],
      },
      evidence,
      { correct_diagnosis: "database saturation" },
    );

    expect(result.hypotheses[0]).toMatchObject({ id: "h0", status: "supported" });
    expect(result.hypotheses[1]).toMatchObject({ id: "h1", status: "contradicted" });
    expect(result.evidence[0]?.supports).toContain("h0");
    expect(result.evidence[1]?.contradicts).toContain("h1");
  });

  it("builds conclusion, unknowns, and next steps from evaluated hypotheses", () => {
    const hypotheses = [
      { id: "h0", statement: "Database saturation caused the outage", status: "supported", confidence: 0.8 },
      { id: "h1", statement: "Traffic spike caused the outage", status: "unresolved", confidence: 0.4 },
    ] as const;
    const evidence = [
      {
        id: "db",
        kind: "observation",
        source: "scenario execution",
        summary: "Database saturation detected",
        supports: ["h0"],
        contradicts: [],
        isRedHerring: false,
      },
      {
        id: "cache",
        kind: "red_herring",
        source: "scenario execution",
        summary: "Cache warning on unrelated node",
        supports: [],
        contradicts: ["h1"],
        isRedHerring: true,
      },
    ];

    expect(buildInvestigationConclusion([...hypotheses], [...evidence])).toEqual({
      bestExplanation: "Database saturation caused the outage",
      confidence: 0.8,
      limitations: [
        "1 potential red herring(s) in evidence pool",
        "Some hypotheses remain unresolved",
        "Investigation based on generated scenario — not live system data",
      ],
    });

    const unknowns = identifyInvestigationUnknowns([...hypotheses], [...evidence]);
    expect(unknowns).toContain('Hypothesis "Traffic spike caused the outage" needs more evidence');
    expect(unknowns).toContain("Limited evidence collected — more data sources needed");

    expect(recommendInvestigationNextSteps([...hypotheses], unknowns)).toEqual([
      'Verify leading hypothesis: "Database saturation caused the outage"',
      'Gather evidence for: "Traffic spike caused the outage"',
      "Address identified unknowns before concluding",
    ]);
  });
});
