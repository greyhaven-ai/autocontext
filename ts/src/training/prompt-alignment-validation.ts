import {
  extractPromptSections,
  measurePromptWordOverlap,
} from "./prompt-alignment-helpers.js";
import type {
  AlignmentReport,
  PromptPair,
} from "./prompt-alignment-types.js";

export function validatePromptAlignmentReport(opts: {
  trainingPrompt: PromptPair;
  runtimePrompt: PromptPair;
}): AlignmentReport {
  const trainingSections = extractPromptSections(opts.trainingPrompt.system);
  const runtimeSections = extractPromptSections(opts.runtimePrompt.system);
  const mismatches: string[] = [];

  for (const section of runtimeSections) {
    if (!trainingSections.includes(section)) {
      mismatches.push(`Section '${section}' present in runtime but missing from training`);
    }
  }

  for (const section of trainingSections) {
    if (!runtimeSections.includes(section)) {
      mismatches.push(`Section '${section}' present in training but missing from runtime`);
    }
  }

  if (opts.trainingPrompt.user !== opts.runtimePrompt.user) {
    const similarity = measurePromptWordOverlap(
      opts.trainingPrompt.user,
      opts.runtimePrompt.user,
    );
    if (similarity < 0.5) {
      mismatches.push("User prompts differ significantly between training and runtime");
    }
  }

  return {
    aligned: mismatches.length === 0,
    mismatches,
    trainingSections,
    runtimeSections,
  };
}
