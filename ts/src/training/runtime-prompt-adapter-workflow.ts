import type { PromptPair } from "./prompt-alignment-types.js";

export function adaptRuntimePromptBundle(bundle: { competitor: string }): PromptPair {
  const prompt = bundle.competitor;
  const taskMarker = "## Your Task";
  const taskIndex = prompt.indexOf(taskMarker);

  if (taskIndex >= 0) {
    return {
      system: prompt.slice(0, taskIndex).trim(),
      user: prompt.slice(taskIndex + taskMarker.length).trim(),
    };
  }

  const parts = prompt.split("\n\n");
  if (parts.length >= 2) {
    return {
      system: parts.slice(0, -1).join("\n\n").trim(),
      user: parts[parts.length - 1].trim(),
    };
  }

  return { system: prompt, user: "" };
}
