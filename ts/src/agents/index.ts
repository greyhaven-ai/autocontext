/**
 * Agent orchestration module (AC-345).
 */

export {
  ROLES,
  ROLE_CONFIGS,
  parseCompetitorOutput,
  parseAnalystOutput,
  parseCoachOutput,
  parseArchitectOutput,
  extractDelimitedSection,
} from "./roles.js";
export type {
  Role,
  RoleConfig,
  CompetitorOutput,
  AnalystOutput,
  CoachOutput,
  ArchitectOutput,
} from "./roles.js";

export { RuntimeBridgeProvider, RetryProvider } from "./provider-bridge.js";
export type { RetryOpts, RuntimeBridgeProviderOpts } from "./provider-bridge.js";

export { ModelRouter, TierConfig } from "./model-router.js";
export type { TierConfigOpts, SelectOpts } from "./model-router.js";

export { AgentOrchestrator } from "./orchestrator.js";
export type { GenerationPrompts, GenerationResult } from "./orchestrator.js";
