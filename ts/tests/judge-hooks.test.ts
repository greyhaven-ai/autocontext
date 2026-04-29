import { describe, expect, it } from "vitest";

import { HookBus, HookEvents, LLMJudge } from "../src/index.js";
import type { LLMProvider } from "../src/index.js";

function judgeResponse(score: number, reasoning = "Hooked judge result"): string {
  return (
    "<!-- JUDGE_RESULT_START -->\n" +
    JSON.stringify({
      score,
      reasoning,
      dimensions: { clarity: score },
    }) +
    "\n<!-- JUDGE_RESULT_END -->"
  );
}

describe("LLMJudge extension hooks", () => {
  it("fires before_judge and after_judge around real provider judge calls", async () => {
    const providerPrompts: string[] = [];
    const provider: LLMProvider = {
      name: "hook-provider",
      defaultModel: () => "hook-model",
      complete: async (opts) => {
        providerPrompts.push(opts.userPrompt);
        return {
          text: judgeResponse(0.1, "provider raw score"),
          model: "hook-model",
          usage: {},
        };
      },
    };
    const bus = new HookBus();
    const seen: string[] = [];

    bus.on(HookEvents.BEFORE_JUDGE, (event) => {
      seen.push(`before:${event.payload.sample}:${event.payload.attempt}`);
      return {
        userPrompt: `${event.payload.userPrompt}\nHooked judge instruction`,
      };
    });
    bus.on(HookEvents.AFTER_JUDGE, (event) => {
      seen.push(`after:${event.payload.sample}:${event.payload.attempt}`);
      return {
        response_text: judgeResponse(0.77),
      };
    });

    const judge = new LLMJudge({
      provider,
      model: "hook-model",
      rubric: "Score clarity.",
      hookBus: bus,
    });
    const result = await judge.evaluate({
      taskPrompt: "Write a summary.",
      agentOutput: "Summary.",
    });

    expect(providerPrompts).toEqual([expect.stringContaining("Hooked judge instruction")]);
    expect(seen).toEqual(["before:1:1", "after:1:1"]);
    expect(result.score).toBe(0.77);
    expect(result.rawResponses).toEqual([judgeResponse(0.77)]);
  });

  it("threads judge hooks through saved agent-task solve evaluations", async () => {
    const provider: LLMProvider = {
      name: "agent-task-provider",
      defaultModel: () => "agent-task-model",
      complete: async (opts) => {
        if (opts.userPrompt.includes("## Agent Output")) {
          return {
            text: judgeResponse(0.66),
            model: "agent-task-model",
            usage: {},
          };
        }
        return {
          text: "Initial answer",
          model: "agent-task-model",
          usage: {},
        };
      },
    };
    const bus = new HookBus();
    const seen: string[] = [];
    bus.on(HookEvents.BEFORE_JUDGE, () => {
      seen.push("before_judge");
    });
    bus.on(HookEvents.AFTER_JUDGE, () => {
      seen.push("after_judge");
    });
    const { executeAgentTaskSolve } =
      await import("../src/knowledge/agent-task-solve-execution.js");

    const result = await executeAgentTaskSolve({
      provider,
      hookBus: bus,
      created: {
        name: "hooked_task",
        spec: {
          taskPrompt: "Write a concise answer.",
          judgeRubric: "Score clarity.",
          outputFormat: "free_text",
        },
      },
      generations: 1,
    });

    expect(seen).toEqual(["before_judge", "after_judge"]);
    expect(result.result.best_score).toBe(0.66);
  });
});
