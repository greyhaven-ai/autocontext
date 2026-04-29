import { describe, expect, it, afterEach } from "vitest";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { HookBus, HookEvents } from "../src/extensions/index.js";

function makeRoot(): string {
  return mkdtempSync(join(tmpdir(), "autoctx-runner-hooks-"));
}

describe("GenerationRunner extension hooks", () => {
  const roots: string[] = [];
  afterEach(() => {
    for (const root of roots.splice(0)) {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("mutates prompt context and observes provider requests/responses in a real run", async () => {
    const { GenerationRunner } = await import("../src/loop/generation-runner.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const { SQLiteStore } = await import("../src/storage/index.js");

    class RecordingProvider {
      readonly name = "recording";
      prompts: string[] = [];

      defaultModel(): string {
        return "recording-model";
      }

      async complete(opts: { userPrompt: string }): Promise<{ text: string; model: string; usage: Record<string, number> }> {
        this.prompts.push(opts.userPrompt);
        if (opts.userPrompt.includes("Describe your strategy")) {
          return {
            text: JSON.stringify({ aggression: 0.60, defense: 0.55, path_bias: 0.50 }),
            model: "recording-model",
            usage: { output_tokens: 4 },
          };
        }
        if (opts.userPrompt.includes("Analyze strengths/failures")) {
          return {
            text: "## Findings\n\n- Hooked run stayed stable.",
            model: "recording-model",
            usage: {},
          };
        }
        return {
          text:
            "<!-- PLAYBOOK_START -->\n" +
            "## Strategy Updates\n\n- Hooked coach preserved defender coverage.\n" +
            "<!-- PLAYBOOK_END -->\n\n" +
            "<!-- LESSONS_START -->\n- Extension context can shape prompt state.\n<!-- LESSONS_END -->\n\n" +
            "<!-- COMPETITOR_HINTS_START -->\n- Keep defense above 0.5.\n<!-- COMPETITOR_HINTS_END -->",
          model: "recording-model",
          usage: {},
        };
      }
    }

    const root = makeRoot();
    roots.push(root);
    const provider = new RecordingProvider();
    const bus = new HookBus();
    const seenEvents: string[] = [];
    bus.on(HookEvents.GENERATION_START, () => {
      seenEvents.push("generation_start");
    });
    bus.on(HookEvents.GENERATION_END, () => {
      seenEvents.push("generation_end");
    });
    bus.on(HookEvents.CONTEXT_COMPONENTS, (event) => {
      seenEvents.push("context_components");
      return {
        components: {
          ...readStringRecord(event.payload.components),
          playbook: "hook playbook guidance",
          session_reports: "hook session report context",
        },
      };
    });
    bus.on(HookEvents.BEFORE_COMPACTION, () => {
      seenEvents.push("before_compaction");
    });
    bus.on(HookEvents.AFTER_COMPACTION, () => {
      seenEvents.push("after_compaction");
    });
    bus.on(HookEvents.CONTEXT, (event) => {
      seenEvents.push("context");
      const roles = readStringRecord(event.payload.roles);
      return {
        roles: {
          ...roles,
          competitor: `${roles.competitor}\nhook final context`,
        },
      };
    });
    bus.on(HookEvents.BEFORE_PROVIDER_REQUEST, (event) => {
      seenEvents.push(`before_provider:${event.payload.role}`);
      if (event.payload.role === "competitor") {
        return { userPrompt: `${event.payload.userPrompt}\nhook provider request` };
      }
      return undefined;
    });
    bus.on(HookEvents.AFTER_PROVIDER_RESPONSE, (event) => {
      seenEvents.push(`after_provider:${event.payload.role}`);
      return { metadata: { hookObserved: true } };
    });

    const store = new SQLiteStore(join(root, "test.db"));
    store.migrate(join(import.meta.dirname, "..", "migrations"));
    const runner = new GenerationRunner({
      provider,
      scenario: new GridCtfScenario(),
      store,
      runsRoot: join(root, "runs"),
      knowledgeRoot: join(root, "knowledge"),
      matchesPerGeneration: 2,
      maxRetries: 0,
      minDelta: 0.0,
      hookBus: bus,
    });

    await runner.run("hook-run", 1);

    const competitorProviderPrompt = provider.prompts.find((prompt) =>
      prompt.includes("Describe your strategy"),
    );
    expect(competitorProviderPrompt).toContain("hook playbook guidance");
    expect(competitorProviderPrompt).toContain("hook session report context");
    expect(competitorProviderPrompt).toContain("hook final context");
    expect(competitorProviderPrompt).toContain("hook provider request");

    const artifactPrompt = readFileSync(
      join(root, "runs", "hook-run", "generations", "gen_1", "competitor_prompt.md"),
      "utf-8",
    );
    expect(artifactPrompt).toContain("hook final context");
    expect(artifactPrompt).not.toContain("hook provider request");
    expect(seenEvents).toEqual(expect.arrayContaining([
      "context_components",
      "generation_start",
      "generation_end",
      "before_compaction",
      "after_compaction",
      "context",
      "before_provider:competitor",
      "after_provider:competitor",
    ]));

    store.close();
  });
});

function readStringRecord(value: unknown): Record<string, string> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return {};
  }
  const result: Record<string, string> = {};
  for (const [key, raw] of Object.entries(value)) {
    if (typeof raw === "string") {
      result[key] = raw;
    }
  }
  return result;
}
