import { describe, expect, it } from "vitest";

import {
  buildFallbackInvestigationHypothesisSet,
  parseInvestigationHypothesisResponse,
  parseInvestigationSpecResponse,
} from "../src/investigation/investigation-generation-parsing.js";

describe("investigation generation parsing", () => {
  it("parses investigation spec responses from provider JSON", () => {
    expect(
      parseInvestigationSpecResponse(
        JSON.stringify({
          description: "Investigate anomaly",
          evidence_pool: [],
          correct_diagnosis: "config drift",
        }),
      ),
    ).toMatchObject({
      description: "Investigate anomaly",
      correct_diagnosis: "config drift",
    });
  });

  it("parses investigation spec responses wrapped in prose and fenced JSON", () => {
    expect(
      parseInvestigationSpecResponse(
        "I found the investigation spec below.\n```json\n" +
          JSON.stringify({
            description: "Investigate anomaly",
            evidence_pool: [],
            correct_diagnosis: "config drift",
          }) +
          "\n```\nThis should help.",
      ),
    ).toMatchObject({
      description: "Investigate anomaly",
      correct_diagnosis: "config drift",
    });
  });

  it("normalizes parsed hypothesis responses and applies limits", () => {
    expect(
      parseInvestigationHypothesisResponse({
        text: JSON.stringify({
          question: "What caused the outage?",
          hypotheses: [
            { statement: "Database saturation", confidence: 1.2 },
            { statement: "Traffic spike", confidence: -1 },
            { confidence: 0.2 },
          ],
        }),
        description: "Investigate outage",
        maxHypotheses: 1,
      }),
    ).toEqual({
      question: "What caused the outage?",
      hypotheses: [{ statement: "Database saturation", confidence: 1 }],
    });
  });

  it("builds the fallback hypothesis set when parsing fails", () => {
    expect(
      buildFallbackInvestigationHypothesisSet({
        description: "Investigate outage",
      }),
    ).toEqual({
      question: "Investigate outage",
      hypotheses: [{ statement: "Investigate: Investigate outage", confidence: 0.5 }],
    });
  });
});
