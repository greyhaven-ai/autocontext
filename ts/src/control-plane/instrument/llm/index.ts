/**
 * A2-I Layer 8 — LLM enhancement barrel export.
 */
export {
  RATIONALE_PROMPT,
  SESSION_SUMMARY_PROMPT,
  type RationaleContext,
  type SessionSummaryContext,
} from "./prompts.js";

export {
  shouldEnableEnhancement,
  hasAnyLLMKey,
  type EnableEnhancementInputs,
} from "./tty-detector.js";

export {
  enhance,
  type EnhancerProvider,
  type EnhancerDiagnostic,
  type EnhanceOpts,
} from "./enhancer.js";
