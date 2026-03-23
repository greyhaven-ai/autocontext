/**
 * Tests for AC-345: Agent Orchestration — Roles, Prompts, Provider Bridge,
 * Model Router, Codex CLI, Orchestrator.
 */

import { describe, it, expect } from "vitest";

// ---------------------------------------------------------------------------
// Task 13: Role Definitions & Output Parsing
// ---------------------------------------------------------------------------

describe("Role definitions", () => {
  it("exports ROLES constant", async () => {
    const { ROLES } = await import("../src/agents/roles.js");
    expect(ROLES).toContain("competitor");
    expect(ROLES).toContain("analyst");
    expect(ROLES).toContain("coach");
    expect(ROLES).toContain("architect");
    expect(ROLES).toContain("translator");
    expect(ROLES).toContain("curator");
  });

  it("exports ROLE_CONFIGS with per-role settings", async () => {
    const { ROLE_CONFIGS } = await import("../src/agents/roles.js");
    expect(ROLE_CONFIGS.competitor.maxTokens).toBe(800);
    expect(ROLE_CONFIGS.competitor.temperature).toBe(0.2);
    expect(ROLE_CONFIGS.coach.maxTokens).toBe(2000);
    expect(ROLE_CONFIGS.architect.temperature).toBe(0.4);
  });
});

describe("Output parsing", () => {
  it("parseCompetitorOutput creates typed output", async () => {
    const { parseCompetitorOutput } = await import("../src/agents/roles.js");
    const output = parseCompetitorOutput("raw text", { aggression: 0.8 });
    expect(output.rawText).toBe("raw text");
    expect(output.strategy.aggression).toBe(0.8);
    expect(output.isCodeStrategy).toBe(false);
  });

  it("parseAnalystOutput extracts section bullets", async () => {
    const { parseAnalystOutput } = await import("../src/agents/roles.js");
    const md = `## Findings\n- Finding one\n- Finding two\n## Root Causes\n- Cause one\n## Actionable Recommendations\n- Do this`;
    const output = parseAnalystOutput(md);
    expect(output.findings).toEqual(["Finding one", "Finding two"]);
    expect(output.rootCauses).toEqual(["Cause one"]);
    expect(output.recommendations).toEqual(["Do this"]);
    expect(output.parseSuccess).toBe(true);
  });

  it("parseAnalystOutput returns empty arrays for missing sections", async () => {
    const { parseAnalystOutput } = await import("../src/agents/roles.js");
    const output = parseAnalystOutput("No structure here");
    expect(output.findings).toEqual([]);
    expect(output.rootCauses).toEqual([]);
    expect(output.recommendations).toEqual([]);
  });

  it("parseCoachOutput extracts delimited sections", async () => {
    const { parseCoachOutput } = await import("../src/agents/roles.js");
    const md = [
      "<!-- PLAYBOOK_START -->",
      "Playbook content here",
      "<!-- PLAYBOOK_END -->",
      "<!-- LESSONS_START -->",
      "Lesson 1",
      "<!-- LESSONS_END -->",
      "<!-- COMPETITOR_HINTS_START -->",
      "Hint: be aggressive",
      "<!-- COMPETITOR_HINTS_END -->",
    ].join("\n");
    const output = parseCoachOutput(md);
    expect(output.playbook).toContain("Playbook content here");
    expect(output.lessons).toContain("Lesson 1");
    expect(output.hints).toContain("be aggressive");
    expect(output.parseSuccess).toBe(true);
  });

  it("parseCoachOutput falls back to entire content as playbook", async () => {
    const { parseCoachOutput } = await import("../src/agents/roles.js");
    const output = parseCoachOutput("Just a plain playbook");
    expect(output.playbook).toBe("Just a plain playbook");
    expect(output.lessons).toBe("");
    expect(output.hints).toBe("");
  });

  it("parseArchitectOutput extracts tool specs from JSON", async () => {
    const { parseArchitectOutput } = await import("../src/agents/roles.js");
    const md = 'Here are tools:\n```json\n{"tools": [{"name": "validator", "description": "validates", "code": "def f(): pass"}]}\n```';
    const output = parseArchitectOutput(md);
    expect(output.toolSpecs).toHaveLength(1);
    expect(output.toolSpecs[0].name).toBe("validator");
    expect(output.parseSuccess).toBe(true);
  });

  it("parseArchitectOutput returns empty on no tools", async () => {
    const { parseArchitectOutput } = await import("../src/agents/roles.js");
    const output = parseArchitectOutput("No tools here");
    expect(output.toolSpecs).toEqual([]);
  });

  it("extractDelimitedSection extracts between markers", async () => {
    const { extractDelimitedSection } = await import("../src/agents/roles.js");
    const text = "before <!-- START -->content here<!-- END --> after";
    expect(extractDelimitedSection(text, "<!-- START -->", "<!-- END -->")).toBe("content here");
  });

  it("extractDelimitedSection returns null when missing", async () => {
    const { extractDelimitedSection } = await import("../src/agents/roles.js");
    expect(extractDelimitedSection("no markers", "<!-- START -->", "<!-- END -->")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Task 14: Prompt Template Assembly
// ---------------------------------------------------------------------------

describe("Prompt templates", () => {
  it("exports buildPromptBundle", async () => {
    const { buildPromptBundle } = await import("../src/prompts/templates.js");
    expect(typeof buildPromptBundle).toBe("function");
  });

  it("builds bundle with all role prompts", async () => {
    const { buildPromptBundle } = await import("../src/prompts/templates.js");
    const bundle = buildPromptBundle({
      scenarioRules: "20x20 grid game",
      strategyInterface: "JSON with aggression, defense, path_bias",
      evaluationCriteria: "capture progress",
      playbook: "Be aggressive",
      trajectory: "| Gen | Score |\n| 1 | 0.5 |",
      lessons: "Lesson 1",
      tools: "",
      hints: "Try flanking",
      analysis: "",
    });
    expect(bundle.competitor).toContain("20x20 grid game");
    expect(bundle.competitor).toContain("JSON with aggression");
    expect(bundle.analyst).toContain("Be aggressive");
    expect(bundle.coach).toContain("PLAYBOOK_START");
    expect(bundle.architect).toBeDefined();
  });

  it("includes trajectory in prompts", async () => {
    const { buildPromptBundle } = await import("../src/prompts/templates.js");
    const bundle = buildPromptBundle({
      scenarioRules: "rules",
      strategyInterface: "interface",
      evaluationCriteria: "criteria",
      playbook: "playbook",
      trajectory: "## Score Trajectory\n| Gen | Mean |",
      lessons: "",
      tools: "",
      hints: "",
      analysis: "",
    });
    expect(bundle.competitor).toContain("Score Trajectory");
  });
});

// ---------------------------------------------------------------------------
// Task 15: Provider Bridge + RetryProvider
// ---------------------------------------------------------------------------

describe("Provider Bridge", () => {
  it("exports RuntimeBridgeProvider", async () => {
    const { RuntimeBridgeProvider } = await import("../src/agents/provider-bridge.js");
    expect(RuntimeBridgeProvider).toBeDefined();
  });

  it("adapts AgentRuntime to LLMProvider interface", async () => {
    const { RuntimeBridgeProvider } = await import("../src/agents/provider-bridge.js");
    const mockRuntime = {
      generate: async (prompt: string) => ({ text: `response to: ${prompt}`, metadata: {} }),
      revise: async () => ({ text: "revised", metadata: {} }),
    };
    const provider = new RuntimeBridgeProvider(mockRuntime as any, "test-model");
    expect(provider.name).toBe("runtime-bridge");
    expect(provider.defaultModel()).toBe("test-model");
    const result = await provider.complete({
      systemPrompt: "system",
      userPrompt: "hello",
    });
    expect(result.text).toContain("response to:");
  });
});

describe("RetryProvider", () => {
  it("exports RetryProvider", async () => {
    const { RetryProvider } = await import("../src/agents/provider-bridge.js");
    expect(RetryProvider).toBeDefined();
  });

  it("returns result on first success", async () => {
    const { RetryProvider } = await import("../src/agents/provider-bridge.js");
    let calls = 0;
    const inner = {
      name: "test",
      defaultModel: () => "model",
      complete: async () => { calls++; return { text: "ok", usage: {} }; },
    };
    const provider = new RetryProvider(inner as any, { maxRetries: 3 });
    const result = await provider.complete({ systemPrompt: "", userPrompt: "test" });
    expect(result.text).toBe("ok");
    expect(calls).toBe(1);
  });

  it("retries on failure then succeeds", async () => {
    const { RetryProvider } = await import("../src/agents/provider-bridge.js");
    let calls = 0;
    const inner = {
      name: "test",
      defaultModel: () => "model",
      complete: async () => {
        calls++;
        if (calls < 3) throw new Error("transient");
        return { text: "recovered", usage: {} };
      },
    };
    const provider = new RetryProvider(inner as any, { maxRetries: 3, baseDelay: 0 });
    const result = await provider.complete({ systemPrompt: "", userPrompt: "test" });
    expect(result.text).toBe("recovered");
    expect(calls).toBe(3);
  });

  it("throws after exhausting retries", async () => {
    const { RetryProvider } = await import("../src/agents/provider-bridge.js");
    const inner = {
      name: "test",
      defaultModel: () => "model",
      complete: async () => { throw new Error("permanent"); },
    };
    const provider = new RetryProvider(inner as any, { maxRetries: 2, baseDelay: 0 });
    await expect(provider.complete({ systemPrompt: "", userPrompt: "test" })).rejects.toThrow("permanent");
  });
});

// ---------------------------------------------------------------------------
// Task 16: Model Router
// ---------------------------------------------------------------------------

describe("ModelRouter", () => {
  it("exports ModelRouter and TierConfig", async () => {
    const { ModelRouter, TierConfig } = await import("../src/agents/model-router.js");
    expect(ModelRouter).toBeDefined();
    expect(TierConfig).toBeDefined();
  });

  it("returns null when disabled", async () => {
    const { ModelRouter, TierConfig } = await import("../src/agents/model-router.js");
    const config = new TierConfig({ enabled: false });
    const router = new ModelRouter(config);
    expect(router.select("competitor", { generation: 1, retryCount: 0, isPlateau: false })).toBeNull();
  });

  it("routes competitor to haiku for early generations", async () => {
    const { ModelRouter, TierConfig } = await import("../src/agents/model-router.js");
    const config = new TierConfig({ enabled: true });
    const router = new ModelRouter(config);
    const model = router.select("competitor", { generation: 1, retryCount: 0, isPlateau: false });
    expect(model).toContain("haiku");
  });

  it("escalates competitor to sonnet after haiku_max_gen", async () => {
    const { ModelRouter, TierConfig } = await import("../src/agents/model-router.js");
    const config = new TierConfig({ enabled: true, competitorHaikuMaxGen: 3 });
    const router = new ModelRouter(config);
    const model = router.select("competitor", { generation: 5, retryCount: 0, isPlateau: false });
    expect(model).toContain("sonnet");
  });

  it("escalates competitor to sonnet on retry", async () => {
    const { ModelRouter, TierConfig } = await import("../src/agents/model-router.js");
    const config = new TierConfig({ enabled: true });
    const router = new ModelRouter(config);
    const model = router.select("competitor", { generation: 1, retryCount: 2, isPlateau: false });
    expect(model).toContain("sonnet");
  });

  it("escalates competitor to opus on plateau", async () => {
    const { ModelRouter, TierConfig } = await import("../src/agents/model-router.js");
    const config = new TierConfig({ enabled: true });
    const router = new ModelRouter(config);
    const model = router.select("competitor", { generation: 5, retryCount: 0, isPlateau: true });
    expect(model).toContain("opus");
  });

  it("architect always gets opus", async () => {
    const { ModelRouter, TierConfig } = await import("../src/agents/model-router.js");
    const config = new TierConfig({ enabled: true });
    const router = new ModelRouter(config);
    const model = router.select("architect", { generation: 1, retryCount: 0, isPlateau: false });
    expect(model).toContain("opus");
  });

  it("coach escalates to opus on plateau", async () => {
    const { ModelRouter, TierConfig } = await import("../src/agents/model-router.js");
    const config = new TierConfig({ enabled: true });
    const router = new ModelRouter(config);
    const model = router.select("coach", { generation: 1, retryCount: 0, isPlateau: true });
    expect(model).toContain("opus");
  });
});

// ---------------------------------------------------------------------------
// Task 17: Codex CLI Runtime
// ---------------------------------------------------------------------------

describe("CodexCLIRuntime", () => {
  it("exports CodexCLIRuntime and CodexCLIConfig", async () => {
    const { CodexCLIRuntime, CodexCLIConfig } = await import("../src/runtimes/codex-cli.js");
    expect(CodexCLIRuntime).toBeDefined();
    expect(CodexCLIConfig).toBeDefined();
  });

  it("parseOutput handles JSONL events", async () => {
    const { CodexCLIRuntime } = await import("../src/runtimes/codex-cli.js");
    const runtime = new CodexCLIRuntime();
    // Access internal parser for testing
    const result = runtime.parseOutput(
      '{"type": "item.message", "content": [{"text": "hello world"}]}\n'
    );
    expect(result.text).toBe("hello world");
  });

  it("parseOutput handles plain text fallback", async () => {
    const { CodexCLIRuntime } = await import("../src/runtimes/codex-cli.js");
    const runtime = new CodexCLIRuntime();
    const result = runtime.parseOutput("just plain text output");
    expect(result.text).toBe("just plain text output");
  });

  it("parseOutput handles empty input", async () => {
    const { CodexCLIRuntime } = await import("../src/runtimes/codex-cli.js");
    const runtime = new CodexCLIRuntime();
    const result = runtime.parseOutput("");
    expect(result.text).toBe("");
  });

  it("config has correct defaults", async () => {
    const { CodexCLIConfig } = await import("../src/runtimes/codex-cli.js");
    const config = new CodexCLIConfig();
    expect(config.model).toBe("o4-mini");
    expect(config.approvalMode).toBe("full-auto");
    expect(config.timeout).toBe(120.0);
  });

  it("buildArgs constructs correct command", async () => {
    const { CodexCLIRuntime, CodexCLIConfig } = await import("../src/runtimes/codex-cli.js");
    const config = new CodexCLIConfig({ model: "o4-mini", quiet: true, workspace: "/tmp/work" });
    const runtime = new CodexCLIRuntime(config);
    const args = runtime.buildArgs();
    expect(args).toContain("exec");
    expect(args).toContain("--model");
    expect(args).toContain("o4-mini");
    expect(args).toContain("--quiet");
    expect(args).toContain("--cd");
    expect(args).toContain("/tmp/work");
  });
});

// ---------------------------------------------------------------------------
// Task 18: Agent Orchestrator
// ---------------------------------------------------------------------------

describe("AgentOrchestrator", () => {
  it("exports AgentOrchestrator", async () => {
    const { AgentOrchestrator } = await import("../src/agents/orchestrator.js");
    expect(AgentOrchestrator).toBeDefined();
  });

  it("dispatches roles in correct order", async () => {
    const { AgentOrchestrator } = await import("../src/agents/orchestrator.js");

    const callOrder: string[] = [];
    const mockProvider = {
      name: "mock",
      defaultModel: () => "mock-model",
      complete: async (opts: { systemPrompt: string; userPrompt: string }) => {
        // Detect role from prompt context
        if (opts.userPrompt.includes("[competitor]")) {
          callOrder.push("competitor");
          return { text: '{"aggression": 0.6, "defense": 0.4, "path_bias": 0.5}', usage: {} };
        }
        if (opts.userPrompt.includes("[analyst]")) {
          callOrder.push("analyst");
          return { text: "## Findings\n- Finding 1", usage: {} };
        }
        if (opts.userPrompt.includes("[coach]")) {
          callOrder.push("coach");
          return { text: "<!-- PLAYBOOK_START -->\nplaybook\n<!-- PLAYBOOK_END -->", usage: {} };
        }
        if (opts.userPrompt.includes("[architect]")) {
          callOrder.push("architect");
          return { text: "No tools needed.", usage: {} };
        }
        callOrder.push("unknown");
        return { text: "ok", usage: {} };
      },
    };

    const orchestrator = new AgentOrchestrator(mockProvider as any);
    const result = await orchestrator.runGeneration({
      competitorPrompt: "[competitor] Generate strategy",
      analystPrompt: "[analyst] Analyze",
      coachPrompt: "[coach] Update playbook",
      architectPrompt: "[architect] Propose tools",
    });

    // Competitor must run before analyst/coach/architect
    expect(callOrder.indexOf("competitor")).toBeLessThan(callOrder.indexOf("analyst"));
    expect(callOrder.indexOf("competitor")).toBeLessThan(callOrder.indexOf("coach"));
    expect(result.competitorOutput).toBeDefined();
    expect(result.analystOutput).toBeDefined();
    expect(result.coachOutput).toBeDefined();
  });

  it("returns parsed outputs for each role", async () => {
    const { AgentOrchestrator } = await import("../src/agents/orchestrator.js");

    const mockProvider = {
      name: "mock",
      defaultModel: () => "mock-model",
      complete: async (opts: { userPrompt: string }) => {
        if (opts.userPrompt.includes("[competitor]")) {
          return { text: '{"aggression": 0.7}', usage: {} };
        }
        if (opts.userPrompt.includes("[analyst]")) {
          return { text: "## Findings\n- Good progress", usage: {} };
        }
        if (opts.userPrompt.includes("[coach]")) {
          return {
            text: "<!-- PLAYBOOK_START -->\nBe aggressive\n<!-- PLAYBOOK_END -->\n<!-- LESSONS_START -->\nLesson 1\n<!-- LESSONS_END -->",
            usage: {},
          };
        }
        return { text: "no tools", usage: {} };
      },
    };

    const orchestrator = new AgentOrchestrator(mockProvider as any);
    const result = await orchestrator.runGeneration({
      competitorPrompt: "[competitor] go",
      analystPrompt: "[analyst] go",
      coachPrompt: "[coach] go",
      architectPrompt: "[architect] go",
    });

    expect(result.competitorOutput.rawText).toContain("aggression");
    expect(result.analystOutput.findings).toContain("Good progress");
    expect(result.coachOutput.playbook).toContain("Be aggressive");
    expect(result.coachOutput.lessons).toContain("Lesson 1");
  });

  it("supports per-role providers and models", async () => {
    const { AgentOrchestrator } = await import("../src/agents/orchestrator.js");

    const calls: Array<{ role: string; model?: string }> = [];
    let defaultCalls = 0;

    const defaultProvider = {
      name: "default",
      defaultModel: () => "default-model",
      complete: async () => {
        defaultCalls++;
        return { text: "{}", usage: {} };
      },
    };

    const providerFor = (role: string, text: string) => ({
      name: `${role}-provider`,
      defaultModel: () => `${role}-default`,
      complete: async (opts: { model?: string }) => {
        calls.push({ role, model: opts.model });
        return { text, usage: {}, model: opts.model };
      },
    });

    const orchestrator = new AgentOrchestrator(defaultProvider as any, {
      roleProviders: {
        competitor: providerFor("competitor", '{"aggression": 0.9}') as any,
        analyst: providerFor("analyst", "## Findings\n- Strong opening") as any,
        coach: providerFor("coach", "<!-- PLAYBOOK_START -->\nplaybook\n<!-- PLAYBOOK_END -->") as any,
        architect: providerFor("architect", "No tools needed.") as any,
      },
      roleModels: {
        competitor: "competitor-model",
        analyst: "analyst-model",
        coach: "coach-model",
        architect: "architect-model",
      },
    });

    const result = await orchestrator.runGeneration({
      competitorPrompt: "[competitor] go",
      analystPrompt: "[analyst] go",
      coachPrompt: "[coach] go",
      architectPrompt: "[architect] go",
    });

    expect(defaultCalls).toBe(0);
    expect(calls).toEqual([
      { role: "competitor", model: "competitor-model" },
      { role: "analyst", model: "analyst-model" },
      { role: "coach", model: "coach-model" },
      { role: "architect", model: "architect-model" },
    ]);
    expect(result.competitorOutput.rawText).toContain("aggression");
    expect(result.analystOutput.findings).toContain("Strong opening");
  });
});
