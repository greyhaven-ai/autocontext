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
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { asDbPath, asRunId } from "../src/domain/ids.js";
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

  it("emits the skipped-update diagnostic from a real generation run", async () => {
    const { EventStreamEmitter } = await import("../src/loop/events.js");
    const { GenerationRunner } = await import("../src/loop/generation-runner.js");
    const { DeterministicProvider } = await import("../src/providers/deterministic.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const { SQLiteStore } = await import("../src/storage/index.js");

    class UnformattedCoachProvider extends DeterministicProvider {
      override async complete(
        opts: Parameters<InstanceType<typeof DeterministicProvider>["complete"]>[0],
      ) {
        if (opts.userPrompt.toLowerCase().includes("playbook coach")) {
          return {
            text: "Try moving faster and avoid guarded tiles.",
            model: "unformatted-coach",
            usage: {},
          };
        }
        return super.complete(opts);
      }
    }

    const dir = mkdtempSync(join(tmpdir(), "playbook-marker-visibility-"));
    const store = new SQLiteStore(asDbPath(join(dir, "test.db")));
    try {
      store.migrate(join(import.meta.dirname, "..", "migrations"));
      const events = new EventStreamEmitter(join(dir, "events.ndjson"));
      const diagnostics: Array<Record<string, unknown>> = [];
      events.subscribe((event, payload) => {
        if (event === "playbook_update_skipped") diagnostics.push(payload);
      });
      const runner = new GenerationRunner({
        provider: new UnformattedCoachProvider(),
        scenario: new GridCtfScenario(),
        store,
        runsRoot: join(dir, "runs"),
        knowledgeRoot: join(dir, "knowledge"),
        matchesPerGeneration: 1,
        maxRetries: 0,
        minDelta: 0,
        events,
      });

      await runner.run(asRunId("marker-drift"), 1);

      expect(diagnostics).toEqual([
        {
          run_id: "marker-drift",
          scenario: "grid_ctf",
          generation: 1,
          reason: "missing_markers",
          missing_markers: [
            "playbook_start",
            "playbook_end",
            "lessons_start",
            "lessons_end",
            "hints_start",
            "hints_end",
          ],
        },
      ]);
    } finally {
      store.close();
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
