import type { LLMProvider } from "../types/index.js";
import type { AgentTaskSpec } from "./agent-task-spec.js";
import { designAgentTask } from "./agent-task-designer.js";

export async function designAgentTaskWithProvider(opts: {
  description: string;
  provider: LLMProvider;
  model: string;
}): Promise<AgentTaskSpec> {
  const llmFn = async (system: string, user: string): Promise<string> => {
    const result = await opts.provider.complete({
      systemPrompt: system,
      userPrompt: user,
      model: opts.model,
    });
    return result.text;
  };

  return designAgentTask(opts.description, llmFn);
}
