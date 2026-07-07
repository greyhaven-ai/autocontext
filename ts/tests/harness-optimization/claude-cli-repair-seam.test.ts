import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, test, expect } from "vitest";
import { EventStreamEmitter } from "../../src/loop/events.js";
import { repairCliEnvelope } from "../../src/runtimes/claude-cli.js";

// The opt-in AC-878 repair seam wired into ClaudeCLIRuntime.#parseOutput. The
// hard-private parse method delegates to `repairCliEnvelope`, exercised here.

function emitter(): {
  emitter: EventStreamEmitter;
  events: Array<{ event: string; payload: Record<string, unknown> }>;
} {
  const dir = mkdtempSync(join(tmpdir(), "cli-repair-"));
  const em = new EventStreamEmitter(join(dir, "events.ndjson"));
  const events: Array<{ event: string; payload: Record<string, unknown> }> = [];
  em.subscribe((event, payload) => events.push({ event, payload }));
  return { emitter: em, events };
}

describe("repairCliEnvelope (parse seam)", () => {
  test("no repair config is a byte-unchanged no-op (returns null, emits nothing)", () => {
    const { emitter: em, events } = emitter();
    // No gate/scenario => null regardless of a repairable input.
    expect(repairCliEnvelope('{"result": "ok",}', {})).toBeNull();
    expect(
      repairCliEnvelope('{"result": "ok",}', { emitter: em, scenario: "grid_ctf" }),
    ).toBeNull();
    expect(events).toHaveLength(0);
  });

  test("configured but gate disabled is a no-op", () => {
    const { emitter: em, events } = emitter();
    const out = repairCliEnvelope('{"result": "ok",}', {
      gate: { enabled: false, scenarios: "grid_ctf" },
      scenario: "grid_ctf",
      emitter: em,
    });
    expect(out).toBeNull();
    expect(events).toHaveLength(0);
  });

  test("configured but scenario not allowlisted is a no-op", () => {
    const { emitter: em, events } = emitter();
    const out = repairCliEnvelope('{"result": "ok",}', {
      gate: { enabled: true, scenarios: "othello" },
      scenario: "grid_ctf",
      emitter: em,
    });
    expect(out).toBeNull();
    expect(events).toHaveLength(0);
  });

  test("active gate repairs a malformed envelope and emits at the seam", () => {
    const { emitter: em, events } = emitter();
    const out = repairCliEnvelope('{"result": "ok",}', {
      gate: { enabled: true, scenarios: "grid_ctf" },
      scenario: "grid_ctf",
      emitter: em,
    });
    // Trailing-comma envelope is structurally repaired and now parses.
    expect(out).toBe('{"result": "ok"}');
    expect(JSON.parse(out as string).result).toBe("ok");

    // One event per default repair; the applied one carries the scenario.
    expect(events).toHaveLength(3);
    const applied = events.filter((e) => e.event === "repair_applied");
    expect(applied).toHaveLength(1);
    expect(applied[0].payload.scenario).toBe("grid_ctf");
  });

  test("active gate leaves an unrepairable envelope as null (text fallback)", () => {
    const { emitter: em } = emitter();
    const out = repairCliEnvelope("{{{{ not json", {
      gate: { enabled: true, scenarios: "grid_ctf" },
      scenario: "grid_ctf",
      emitter: em,
    });
    expect(out).toBeNull();
  });
});
