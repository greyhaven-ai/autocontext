/**
 * Deterministic provider — canned responses for CI/testing (AC-346 Task 19).
 * Mirrors Python's DeterministicDevClient in agents/llm_client.py.
 */

import type { CompletionResult, LLMProvider } from "../types/index.js";

export class DeterministicProvider implements LLMProvider {
  readonly name = "deterministic";

  defaultModel(): string {
    return "deterministic-dev";
  }

  async complete(opts: {
    systemPrompt: string;
    userPrompt: string;
    model?: string;
    temperature?: number;
    maxTokens?: number;
  }): Promise<CompletionResult> {
    const prompt = opts.userPrompt.toLowerCase();
    let text: string;

    if (prompt.includes("describe your strategy") || prompt.includes("[competitor]")) {
      text = '{"aggression": 0.60, "defense": 0.55, "path_bias": 0.50}';
    } else if (prompt.includes("analyze strengths/failures") || prompt.includes("[analyst]")) {
      text =
        "## Findings\n\n- Strategy balances offense/defense.\n\n" +
        "## Root Causes\n\n- Moderate aggressiveness.\n\n" +
        "## Actionable Recommendations\n\n- Increase defensive weight.";
    } else if (prompt.includes("playbook coach") || prompt.includes("update the playbook") || prompt.includes("[coach]")) {
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
    } else if (prompt.includes("extract the strategy")) {
      text = '{"aggression": 0.60, "defense": 0.55, "path_bias": 0.50}';
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
