/**
 * A2-I Layer 8 — LLM enhancement barrel export.
 */
export {
  RATIONALE_PROMPT,
  FILE_OPT_OUT_TIP_PROMPT,
  SESSION_SUMMARY_PROMPT,
  type RationaleContext,
  type FileOptOutTipContext,
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
