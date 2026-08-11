/**
 * AC-932: a coach response that drops markers must not fail silently.
 *
 * The playbook is the loop's memory. `generation-runner.ts` refuses to update
 * it unless all six markers are present, and until now refused without saying
 * so -- the generation completed normally and the loop simply did not learn.
 *
 * Measured on llama3.1:8b, 10 trials of the real coach instruction: 8 produced
 * all six markers, 2 produced none. One generation in five, not an edge case.
 */
import { describe, expect, it } from "vitest";

import { PLAYBOOK_MARKERS, missingPlaybookMarkers } from "../src/knowledge/playbook.js";

const WELL_FORMED = [
  `${PLAYBOOK_MARKERS.PLAYBOOK_START}\nP\n${PLAYBOOK_MARKERS.PLAYBOOK_END}`,
  `${PLAYBOOK_MARKERS.LESSONS_START}\nL\n${PLAYBOOK_MARKERS.LESSONS_END}`,
  `${PLAYBOOK_MARKERS.HINTS_START}\nH\n${PLAYBOOK_MARKERS.HINTS_END}`,
].join("\n\n");

describe("playbook marker visibility", () => {
  it("reports nothing missing for a well-formed response", () => {
    expect(missingPlaybookMarkers(WELL_FORMED)).toEqual([]);
  });

  it("names all six when the model ignored the format entirely", () => {
    // The observed 2-in-10 case: readable advice, no markers at all.
    const prose = "Sure! Here's my advice:\n\nTry moving faster and avoid the guarded tiles.";
    expect(missingPlaybookMarkers(prose)).toEqual([
      "playbook_start",
      "playbook_end",
      "lessons_start",
      "lessons_end",
      "hints_start",
      "hints_end",
    ]);
  });

  it("names exactly the one that is missing", () => {
    // The failure mode a boolean hides: five of six present is still a dropped
    // update, and without the name nobody can tell which half broke.
    const almost = WELL_FORMED.replace(PLAYBOOK_MARKERS.HINTS_END, "");
    expect(missingPlaybookMarkers(almost)).toEqual(["hints_end"]);
  });

  it("distinguishes a truncated response from an unformatted one", () => {
    // START without END is truncation (AC-904's signature); no markers at all
    // is a model ignoring the contract. Different causes, different fixes, and
    // the missing-marker list is what tells them apart.
    const truncated = `${PLAYBOOK_MARKERS.PLAYBOOK_START}\nhalf a play`;
    const missing = missingPlaybookMarkers(truncated);
    expect(missing).toContain("playbook_end");
    expect(missing).not.toContain("playbook_start");
  });
});
