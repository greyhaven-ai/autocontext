/**
 * Deterministic provider — canned responses for CI/testing (AC-346 Task 19).
 * Mirrors Python's DeterministicDevClient in agents/llm_client.py.
 */

import {
  ProviderError,
  type CompletionOptions,
  type CompletionResult,
  type LLMProvider,
} from "../types/index.js";

export class DeterministicProvider implements LLMProvider {
  readonly name = "deterministic";
  readonly supportsThinkingStream = false;

  defaultModel(): string {
    return "deterministic-dev";
  }

  supportsImageAttachments(): boolean {
    return false;
  }

  async complete(opts: CompletionOptions): Promise<CompletionResult> {
    if (opts.imageAttachments?.length) {
      throw new ProviderError("Deterministic provider does not support image attachments");
    }
    const prompt = opts.userPrompt.toLowerCase();
    const systemPrompt = opts.systemPrompt.toLowerCase();
    let text: string;

    if (systemPrompt.includes("expert judge evaluating")) {
      const isRevision = prompt.includes("deterministic revised task result");
      text = [
        "<!-- JUDGE_RESULT_START -->",
        JSON.stringify({
          score: isRevision ? 0.92 : 0.72,
          reasoning: isRevision
            ? "The revised deterministic response addresses the requested task and feedback."
            : "The deterministic draft is usable but should be revised for specificity.",
          dimensions: {
            task_completion: isRevision ? 0.92 : 0.72,
          },
        }),
        "<!-- JUDGE_RESULT_END -->",
      ].join("\n");
    } else if (
      systemPrompt.includes("revis") &&
      prompt.includes("## original output") &&
      prompt.includes("## judge feedback")
    ) {
      text =
        "Deterministic revised task result. The response incorporates the evaluation feedback, " +
        "uses the supplied context, and states a concrete, actionable conclusion.";
    } else if (
      systemPrompt.includes("helpful assistant") &&
      !prompt.includes("continue the artifact")
    ) {
      text =
        "Deterministic task result. The response addresses the requested task using the supplied " +
        "context and provides a concise conclusion with actionable next steps.";
    } else if (prompt.includes("describe your strategy") || prompt.includes("[competitor]")) {
      text = deterministicStrategyForPrompt(prompt);
    } else if (prompt.includes("analyze strengths/failures") || prompt.includes("[analyst]")) {
      text =
        "## Findings\n\n- Strategy balances offense/defense.\n\n" +
        "## Root Causes\n\n- Moderate aggressiveness.\n\n" +
        "## Actionable Recommendations\n\n- Increase defensive weight.";
    } else if (prompt.includes("curator") && prompt.includes("consolidat")) {
      text =
        "Consolidated lessons after removing duplicates and stale guidance.\n\n" +
        "<!-- CONSOLIDATED_LESSONS_START -->\n" +
        "- Preserve a defensive anchor above 0.5.\n" +
        "- Keep aggression balanced with defense to avoid unstable regressions.\n" +
        "<!-- CONSOLIDATED_LESSONS_END -->\n" +
        "<!-- LESSONS_REMOVED: 1 -->";
    } else if (prompt.includes("curator") && prompt.includes("playbook quality")) {
      text =
        "The proposed playbook keeps the useful structure and adds clearer guidance.\n\n" +
        "<!-- CURATOR_DECISION: accept -->\n" +
        "<!-- CURATOR_SCORE: 7 -->";
    } else if (
      prompt.includes("playbook coach") ||
      prompt.includes("update the playbook") ||
      prompt.includes("[coach]")
    ) {
      text =
        "<!-- PLAYBOOK_START -->\n" +
        "## Strategy Updates\n\n- Keep defensive anchor.\n- Balance aggression with proportional defense.\n\n" +
        "<!-- PLAYBOOK_END -->\n\n" +
        "<!-- LESSONS_START -->\n" +
        "- When aggression exceeds 0.7 without proportional defense, win rate drops.\n" +
        "<!-- LESSONS_END -->\n\n" +
        "<!-- COMPETITOR_HINTS_START -->\n" +
        "- Try aggression=0.60 with defense=0.55 for balanced scoring.\n" +
        "<!-- COMPETITOR_HINTS_END -->";
    } else if (
      prompt.includes("repair the invalid competitor strategy") ||
      prompt.includes("extract the strategy")
    ) {
      text = deterministicStrategyForPrompt(prompt);
    } else if (
      prompt.includes("investigate") ||
      prompt.includes("root cause") ||
      prompt.includes("outage") ||
      prompt.includes("production incident")
    ) {
      text = JSON.stringify({
        description: "Investigate a production outage using evidence logs",
        environment_description: "Production environment with multiple services",
        initial_state_description: "Outage detected, services degraded",
        success_criteria: ["root cause identified", "remediation proposed"],
        failure_modes: ["misdiagnosis", "incomplete analysis"],
        max_steps: 10,
        actions: [
          {
            name: "gather_logs",
            description: "Collect relevant system logs",
            parameters: {},
            preconditions: [],
            effects: ["logs_available"],
          },
          {
            name: "analyze_metrics",
            description: "Analyze performance metrics",
            parameters: {},
            preconditions: ["gather_logs"],
            effects: ["metrics_analyzed"],
          },
          {
            name: "identify_root_cause",
            description: "Determine root cause from evidence",
            parameters: {},
            preconditions: ["analyze_metrics"],
            effects: ["root_cause_identified"],
          },
          {
            name: "propose_fix",
            description: "Propose remediation steps",
            parameters: {},
            preconditions: ["identify_root_cause"],
            effects: ["fix_proposed"],
          },
        ],
        evidence_pool: [
          {
            id: "log_001",
            content: "Error spike at 14:32 UTC in auth-service",
            isRedHerring: false,
            relevance: 0.9,
          },
          {
            id: "log_002",
            content: "Network latency increase on east-1",
            isRedHerring: true,
            relevance: 0.3,
          },
        ],
        correct_diagnosis: "Auth service token validation failure due to expired signing key",
      });
    } else {
      // Default architect response
      const toolsPayload = {
        tools: [
          {
            name: "threat_assessor",
            description: "Estimate tactical risk.",
            code: "def run(inputs): return {'risk': 0.5}",
          },
        ],
      };
      text =
        "## Observed Bottlenecks\n\n- Need richer replay telemetry.\n\n" +
        "## Tool Proposals\n\n- Add analyzers for tactical confidence.\n\n" +
        `\`\`\`json\n${JSON.stringify(toolsPayload, null, 2)}\n\`\`\``;
    }

    return {
      text,
      model: opts.model ?? "deterministic-dev",
      usage: {
        input_tokens: Math.max(1, Math.floor(opts.userPrompt.length / 6)),
        output_tokens: Math.max(1, Math.floor(text.length / 6)),
      },
    };
  }
}

function deterministicStrategyForPrompt(prompt: string): string {
  if (
    prompt.includes("resource_trader") ||
    (prompt.includes("`buy`") && prompt.includes("`sell`") && prompt.includes("`amount`"))
  ) {
    return '{"buy":"wood","sell":"stone","amount":1}';
  }
  if (
    prompt.includes("othello") ||
    prompt.includes("mobility_weight") ||
    prompt.includes("corner_weight")
  ) {
    return '{"mobility_weight":0.35,"corner_weight":0.45,"stability_weight":0.40}';
  }
  return '{"aggression":0.60,"defense":0.55,"path_bias":0.50}';
}
