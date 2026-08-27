import {
  closeSync,
  existsSync,
  fstatSync,
  openSync,
  readFileSync,
  readSync,
} from "node:fs";

import type { EventStreamRecord } from "../loop/events.js";
import { readEventTraceSpan } from "../loop/event-trace.js";

export const SYSTEM_MAP_PROJECTION = "system-map";
export const SYSTEM_MAP_TRANSFER_EVENT = "system_map_transfer";
export const SYSTEM_MAP_REPLAY_LIMIT = 250;
export const SYSTEM_MAP_MAX_REPLAY_LIMIT = 2_000;
const SYSTEM_MAP_MAX_REPLAY_BYTES = 8 * 1024 * 1024;
const MAX_SUMMARY_VALUE_LENGTH = 160;

export type SystemMapBuildingKind = "rack" | "slab" | "tower" | "vault";
export type SystemMapTransferStatus = "completed" | "failed" | "retry" | "started";
export type SystemMapDistrictColor = "blue" | "purple" | "amber" | "green" | "pink" | "cyan";
export type SystemMapView = "activation" | "context" | "execution" | "routing";

export interface SystemMapNode {
  id: string;
  label: string;
  group: string;
  kind: SystemMapBuildingKind;
  x: number;
  y: number;
  width: number;
  depth: number;
  height: number;
  source: string;
}

export interface SystemMapEdge {
  id: string;
  from: string;
  to: string;
  label: string;
  feedback?: boolean;
}

export interface SystemMapDistrict {
  id: string;
  label: string;
  group: string;
  color: SystemMapDistrictColor;
  x: number;
  y: number;
  width: number;
  depth: number;
}

export interface SystemMapTopology {
  version: 1;
  view: SystemMapView;
  title: string;
  description: string;
  timelineLabel: string;
  districts: readonly SystemMapDistrict[];
  nodes: readonly SystemMapNode[];
  edges: readonly SystemMapEdge[];
}

export interface SystemMapTransfer {
  version: 1;
  id: string;
  sourceSequence: number;
  timestamp: string;
  runId: string;
  generation?: number;
  attempt?: number;
  traceId: string;
  spanId: string;
  parentSpanId?: string;
  spanName: string;
  spanPhase: "start" | "complete" | "instant";
  startedAt: string;
  endedAt?: string;
  payloadBytes?: number;
  event: string;
  edgeId: string;
  from: string;
  to: string;
  kind: string;
  label: string;
  status: SystemMapTransferStatus;
  durationMs?: number;
  summary: Record<string, string | number | boolean>;
}

export const SYSTEM_MAP_TOPOLOGY: SystemMapTopology = {
  version: 1,
  view: "execution",
  title: "Recursive harness",
  description: "Live events move across the recursive harness boundaries that produced them.",
  timelineLabel: "execution timeline",
  districts: [
    district("entry-control", "Entry + control", "entry and control", "blue", -0.7, -0.5, 8.4, 4.8),
    district("recursive-harness", "Recursive harness", "recursive harness", "purple", -1.2, 5.2, 4.9, 3.1),
    district("agent-role-dag", "Agent role DAG", "agent role DAG", "amber", 5.1, 4.5, 12.2, 6),
    district("persistence", "Persistence", "persistence", "green", -1.2, 10.9, 7.4, 3.8),
    district("evaluation", "Evaluation", "evaluation", "pink", 6.5, 10.9, 8.5, 6.4),
    district("outputs", "Outputs", "outputs", "cyan", 17.6, 8.8, 3.7, 5.2),
  ],
  nodes: [
    node("entry", "CLI / MCP", "entry and control", "slab", 0, 0, 2, 1, 1, "server / cli"),
    node("runtime", "Runtime graph", "entry and control", "rack", 5, 0, 2, 1, 3, "runtimes/component-graph.ts"),
    node("runner", "Run loop", "entry and control", "tower", 4, 2.2, 2.3, 2, 4, "loop/generation-runner.ts"),
    node("knowledge", "Knowledge", "recursive harness", "vault", -0.5, 5.7, 3, 2, 2, "knowledge / context"),
    node("competitor", "Competitor", "agent role DAG", "tower", 5.7, 8, 2.3, 2, 3, "agents/competitor"),
    node("translator", "Translator", "agent role DAG", "slab", 8.8, 6.8, 2.4, 1.2, 2, "agents/translator"),
    node("analyst", "Analyst", "agent role DAG", "tower", 10.2, 4.6, 1.5, 1.5, 3, "agents/analyst"),
    node("architect", "Architect", "agent role DAG", "rack", 13.2, 8.2, 2.2, 1.2, 3, "agents/architect"),
    node("coach", "Coach", "agent role DAG", "slab", 14.7, 5, 2, 1.3, 2, "agents/coach"),
    node("validation", "Validation", "evaluation", "rack", 10.2, 11.1, 3, 1.3, 2, "harness/validation"),
    node("tournament", "Tournament", "evaluation", "vault", 10.9, 14, 3, 2, 3, "loop/tournament"),
    node("gate", "Gate", "evaluation", "tower", 6.8, 14.3, 2, 2, 3, "harness/pipeline/gate"),
    node("curation", "Curation", "persistence", "slab", 3.8, 11.2, 2.2, 1.3, 2, "curator / skeptic"),
    node("persistence", "Persistence", "persistence", "vault", -0.5, 11.8, 3, 2, 2, "storage / artifacts"),
    node("events", "Events", "outputs", "rack", 18, 10.3, 2.2, 1.2, 2, "events / runtime sessions"),
  ],
  edges: [
    edge("entry-runner", "entry", "runner", "goal"),
    edge("runtime-runner", "runtime", "runner", "activation"),
    edge("runner-knowledge", "runner", "knowledge", "generation context"),
    edge("knowledge-competitor", "knowledge", "competitor", "prompt context"),
    edge("competitor-translator", "competitor", "translator", "candidate draft"),
    edge("translator-analyst", "translator", "analyst", "strategy"),
    edge("translator-architect", "translator", "architect", "strategy"),
    edge("analyst-coach", "analyst", "coach", "findings"),
    edge("translator-validation", "translator", "validation", "candidate"),
    edge("validation-tournament", "validation", "tournament", "validated strategy"),
    edge("tournament-gate", "tournament", "gate", "scores"),
    edge("gate-validation", "gate", "validation", "retry evidence", true),
    edge("gate-curation", "gate", "curation", "decision"),
    edge("curation-persistence", "curation", "persistence", "accepted learning"),
    edge("persistence-knowledge", "persistence", "knowledge", "next generation", true),
    edge("runner-events", "runner", "events", "runtime events"),
    edge("persistence-events", "persistence", "events", "durable output"),
  ],
};

export const CONTEXT_LINEAGE_TOPOLOGY: SystemMapTopology = {
  version: 1,
  view: "context",
  title: "Context + memory lineage",
  description: "Follow selected knowledge into prompts, evidence, curation, durable memory, and the next generation.",
  timelineLabel: "lineage timeline",
  districts: [
    district("context-sources", "Context sources", "context sources", "blue", -1, -0.5, 8.2, 6),
    district("context-assembly", "Context assembly", "context assembly", "purple", 8, -0.5, 7.5, 6.2),
    district("candidate-work", "Candidate work", "candidate work", "amber", 15.9, 3, 6.2, 7),
    district("evidence", "Evidence + decision", "evidence and decision", "pink", 8, 7.5, 7.5, 7.3),
    district("synthesis", "Synthesis + guard", "synthesis and guard", "cyan", -0.6, 7.5, 7.5, 7.5),
    district("durable-memory", "Durable memory", "durable memory", "green", 5, 15.2, 14.5, 3.7),
  ],
  nodes: [
    node("goal", "Run goal", "context sources", "slab", 0, 0, 2, 1, 1, "server / cli"),
    node("playbook", "Playbook", "context sources", "vault", 3.5, 0, 2.5, 2, 3, "knowledge/playbook.ts"),
    node("dead-ends", "Dead ends", "context sources", "rack", 0, 3, 2.5, 1.5, 2, "knowledge/dead-end.ts"),
    node("history", "Run history", "context sources", "slab", 4, 3.2, 2.4, 1.4, 2, "knowledge / trajectory"),
    node("selection", "Context selection", "context assembly", "tower", 9, 1, 2.4, 1.6, 3, "knowledge/context-selection-store.ts"),
    node("compaction", "Compaction", "context assembly", "vault", 12.7, 1.2, 2.2, 2, 4, "knowledge/semantic-compaction.ts"),
    node("prompt", "Prompt assembly", "context assembly", "rack", 8.7, 3.6, 3, 1.5, 2, "prompts/templates.ts"),
    node("competitor", "Competitor", "candidate work", "tower", 16.7, 5, 2.4, 2, 4, "agents/competitor"),
    node("candidate", "Candidate", "candidate work", "slab", 20, 6.7, 1.8, 1.5, 2, "loop/generation-attempt-workflow.ts"),
    node("tournament", "Tournament", "evidence and decision", "vault", 12, 8.2, 2.8, 2, 3, "execution/tournament.ts"),
    node("evidence", "Score evidence", "evidence and decision", "rack", 9, 11.5, 2.5, 1.4, 2, "loop/generation-event-coordinator.ts"),
    node("gate", "Gate", "evidence and decision", "tower", 12.8, 12, 2, 2, 4, "loop/generation-attempt-state.ts"),
    node("support", "Analyst + coach", "synthesis and guard", "rack", 0.7, 8.4, 2.8, 1.5, 2, "loop/generation-runner.ts"),
    node("guard", "Playbook guard", "synthesis and guard", "slab", 0.9, 12.3, 2.4, 1.4, 2, "knowledge/playbook.ts"),
    node("curator", "Curator", "synthesis and guard", "tower", 3.9, 10.4, 2.2, 2, 3, "agents/curator"),
    node("pending", "Pending approval", "durable memory", "slab", 6, 15.8, 2.2, 1.2, 2, "knowledge/playbook-approval.ts"),
    node("durable", "Durable playbook", "durable memory", "vault", 9.4, 15.7, 3, 2, 3, "knowledge/playbook.ts"),
    node("artifacts", "Run artifacts", "durable memory", "rack", 13.4, 15.8, 2.5, 1.4, 2, "knowledge/artifact-store.ts"),
    node("next-generation", "Next generation", "durable memory", "slab", 16.5, 15.8, 2, 1.3, 2, "loop/generation-runner.ts"),
  ],
  edges: [
    edge("goal-selection", "goal", "selection", "run objective"),
    edge("playbook-selection", "playbook", "selection", "prior learning"),
    edge("dead-ends-selection", "dead-ends", "selection", "avoidance evidence"),
    edge("history-selection", "history", "selection", "score trajectory"),
    edge("selection-compaction", "selection", "compaction", "selected context"),
    edge("selection-prompt", "selection", "prompt", "bounded context"),
    edge("compaction-prompt", "compaction", "prompt", "compacted context"),
    edge("prompt-competitor", "prompt", "competitor", "assembled prompt"),
    edge("competitor-candidate", "competitor", "candidate", "strategy candidate"),
    edge("candidate-tournament", "candidate", "tournament", "candidate program"),
    edge("tournament-evidence", "tournament", "evidence", "scores + outcomes"),
    edge("evidence-gate", "evidence", "gate", "decision evidence"),
    edge("evidence-support", "evidence", "support", "analysis context"),
    edge("support-guard", "support", "guard", "proposed learning"),
    edge("guard-curator", "guard", "curator", "guarded proposal"),
    edge("guard-durable", "guard", "durable", "accepted update"),
    edge("curator-pending", "curator", "pending", "curated update"),
    edge("pending-durable", "pending", "durable", "approved update"),
    edge("gate-dead-ends", "gate", "dead-ends", "rollback evidence", true),
    edge("gate-artifacts", "gate", "artifacts", "generation record"),
    edge("compaction-artifacts", "compaction", "artifacts", "compaction ledger"),
    edge("durable-next-generation", "durable", "next-generation", "retained knowledge"),
    edge("artifacts-next-generation", "artifacts", "next-generation", "run state"),
    edge("next-generation-selection", "next-generation", "selection", "next context", true),
  ],
};

export const RUNTIME_ACTIVATION_TOPOLOGY: SystemMapTopology = {
  version: 1,
  view: "activation",
  title: "Runtime activation + rollback",
  description: "Trace durable activation transactions through policy, component reconciliation, validation, cutover, and recovery.",
  timelineLabel: "activation timeline",
  districts: [
    district("activation-request", "Activation request", "activation request", "blue", -1, -0.5, 7.2, 5.8),
    district("durable-transaction", "Durable transaction", "durable transaction", "purple", 7, -0.5, 7.2, 6.5),
    district("staged-runtime", "Staged runtime", "staged runtime", "amber", 15, 2, 7.5, 8),
    district("validate-cutover", "Validate + cut over", "validate and cut over", "pink", 7.2, 7.2, 6.8, 7.5),
    district("live-state", "Live state", "live state", "green", 15.2, 11, 7.1, 5.6),
    district("rollback-repair", "Rollback + repair", "rollback and repair", "cyan", -0.8, 8, 7, 7.8),
  ],
  nodes: [
    node("activation-intent", "Activation intent", "activation request", "slab", 0, 0, 2.5, 1.2, 1, "control-plane/activation/types.ts"),
    node("candidate-artifact", "Candidate artifact", "activation request", "vault", 3.4, 0, 2.2, 2, 3, "control-plane/activation/registry-controller.ts"),
    node("target-mode", "Target mode", "activation request", "rack", 0, 3.2, 2.4, 1.3, 2, "control-plane/activation/types.ts"),
    node("effect-policy", "Effect policy", "activation request", "tower", 3.7, 3.1, 1.8, 1.8, 3, "runtimes/effect-policy.ts"),
    node("transaction-journal", "Transaction journal", "durable transaction", "tower", 8, 1.2, 2.4, 2, 4, "control-plane/activation/journal.ts"),
    node("stage-ledger", "Stage ledger", "durable transaction", "rack", 11.2, 1.2, 2.3, 1.3, 3, "control-plane/activation/supervisor.ts"),
    node("active-pointer", "Active pointer", "durable transaction", "vault", 8.1, 3.8, 2.3, 2, 2, "control-plane/activation/registry-adapters.ts"),
    node("recovery-scan", "Recovery scan", "durable transaction", "slab", 11.4, 4.1, 2.1, 1.3, 2, "control-plane/activation/supervisor.ts"),
    node("graph-driver", "Graph driver", "staged runtime", "tower", 15.8, 3.8, 2.5, 2, 4, "control-plane/activation/component-graph-driver.ts"),
    node("dependency-graph", "Dependency graph", "staged runtime", "vault", 19.4, 3.8, 2.2, 2, 3, "runtimes/component-graph.ts"),
    node("capability-bindings", "Capability bindings", "staged runtime", "rack", 15.8, 7.2, 2.8, 1.3, 2, "runtimes/component-graph.ts"),
    node("component-scopes", "Component scopes", "staged runtime", "tower", 19.6, 7.2, 2.1, 2, 3, "runtimes/component-lifecycle.ts"),
    node("validation", "Validation", "validate and cut over", "rack", 8.1, 9, 2.5, 1.4, 3, "control-plane/activation/types.ts"),
    node("observed-state", "Observed state", "validate and cut over", "vault", 11.1, 9, 2.2, 2, 2, "control-plane/activation/types.ts"),
    node("prior-drain", "Prior drain", "validate and cut over", "slab", 8.1, 12.3, 2.3, 1.3, 2, "control-plane/activation/component-graph-driver.ts"),
    node("cutover-gate", "Cutover gate", "validate and cut over", "tower", 11.4, 12.2, 1.9, 2, 4, "control-plane/activation/supervisor.ts"),
    node("live-graph", "Live graph", "live state", "vault", 16, 12.6, 3, 2.2, 3, "runtimes/component-graph.ts"),
    node("registry-state", "Registry state", "live state", "rack", 20, 12.8, 1.7, 1.4, 2, "control-plane/activation/registry-controller.ts"),
    node("prior-disposal", "Prior disposal", "live state", "slab", 17.2, 15.4, 2.4, 1.1, 2, "control-plane/activation/types.ts"),
    node("failure-signal", "Failure signal", "rollback and repair", "tower", 0, 9.8, 2, 2, 3, "control-plane/activation/supervisor.ts"),
    node("unwind-effects", "Unwind effects", "rollback and repair", "rack", 3.5, 9.9, 2.5, 1.4, 3, "runtimes/component-lifecycle.ts"),
    node("restore-baseline", "Restore baseline", "rollback and repair", "vault", 0.3, 13.2, 2.7, 2, 3, "control-plane/activation/component-graph-driver.ts"),
    node("convergence-check", "Convergence check", "rollback and repair", "slab", 3.9, 13.4, 2.1, 1.3, 2, "control-plane/activation/supervisor.ts"),
  ],
  edges: [
    edge("intent-candidate", "activation-intent", "candidate-artifact", "candidate request"),
    edge("candidate-mode", "candidate-artifact", "target-mode", "target mode"),
    edge("mode-policy", "target-mode", "effect-policy", "execution policy"),
    edge("policy-journal", "effect-policy", "transaction-journal", "staged transaction"),
    edge("journal-ledger", "transaction-journal", "stage-ledger", "durable stage"),
    edge("pointer-journal", "active-pointer", "transaction-journal", "prior baseline"),
    edge("ledger-driver", "stage-ledger", "graph-driver", "apply candidate"),
    edge("driver-dependencies", "graph-driver", "dependency-graph", "reconcile revision"),
    edge("dependencies-bindings", "dependency-graph", "capability-bindings", "provider order"),
    edge("bindings-scopes", "capability-bindings", "component-scopes", "activate component"),
    edge("scopes-validation", "component-scopes", "validation", "staged runtime"),
    edge("validation-observed", "validation", "observed-state", "validation result"),
    edge("observed-drain", "observed-state", "prior-drain", "validated candidate"),
    edge("drain-cutover", "prior-drain", "cutover-gate", "ready to cut over"),
    edge("cutover-live", "cutover-gate", "live-graph", "runtime cutover"),
    edge("cutover-pointer", "cutover-gate", "active-pointer", "pointer cutover"),
    edge("live-registry", "live-graph", "registry-state", "active revision"),
    edge("live-disposal", "live-graph", "prior-disposal", "dispose prior"),
    edge("disposal-registry", "prior-disposal", "registry-state", "commit"),
    edge("validation-failure", "validation", "failure-signal", "validation failure", true),
    edge("scopes-failure", "component-scopes", "failure-signal", "activation failure", true),
    edge("bindings-failure", "capability-bindings", "failure-signal", "provider failure", true),
    edge("cutover-failure", "cutover-gate", "failure-signal", "cutover failure", true),
    edge("pointer-recovery", "active-pointer", "recovery-scan", "pointer target"),
    edge("recovery-failure", "recovery-scan", "failure-signal", "repair request", true),
    edge("failure-unwind", "failure-signal", "unwind-effects", "abort candidate"),
    edge("unwind-restore", "unwind-effects", "restore-baseline", "restore prior"),
    edge("restore-convergence", "restore-baseline", "convergence-check", "observed baseline"),
    edge("convergence-live", "convergence-check", "live-graph", "recovered runtime", true),
  ],
};

export const PROVIDER_ROUTING_TOPOLOGY: SystemMapTopology = {
  version: 1,
  view: "routing",
  title: "Provider + model routing",
  description: "Follow role demand through routing policy, provider transport, model execution, reliability paths, and call telemetry.",
  timelineLabel: "routing timeline",
  districts: [
    district("routing-demand", "Call demand", "call demand", "blue", -1, -0.5, 7.2, 5.8),
    district("routing-policy", "Routing policy", "routing policy", "purple", 7, -0.5, 7.2, 6.5),
    district("provider-runtime", "Provider runtime", "provider runtime", "amber", 15, 2, 7.5, 8.2),
    district("model-execution", "Model execution", "model execution", "pink", 7.2, 7.4, 6.8, 7.6),
    district("call-telemetry", "Response + telemetry", "response and telemetry", "green", 15.2, 11, 7.2, 5.8),
    district("routing-reliability", "Reliability", "reliability", "cyan", -0.8, 8, 7, 7.9),
  ],
  nodes: [
    node("run-demand", "Run demand", "call demand", "slab", 0, 0, 2.4, 1.2, 1, "loop/generation-runner.ts"),
    node("role-request", "Role request", "call demand", "tower", 3.3, 0, 2.2, 2, 3, "providers/role-routing.ts"),
    node("input-envelope", "Input envelope", "call demand", "rack", 0, 3.2, 2.6, 1.4, 2, "types/index.ts"),
    node("call-context", "Call context", "call demand", "vault", 3.6, 3.1, 2, 2, 2, "control-plane/runtime/model-router.ts"),
    node("role-router", "Role router", "routing policy", "tower", 8, 1.1, 2.2, 2, 4, "providers/role-routing.ts"),
    node("route-table", "Route table", "routing policy", "rack", 11.2, 1.2, 2.3, 1.4, 3, "providers/role-routing-contract.generated.ts"),
    node("model-router", "Model router", "routing policy", "vault", 8.1, 3.8, 2.3, 2, 3, "control-plane/runtime/model-router.ts"),
    node("guardrails", "Route guardrails", "routing policy", "slab", 11.4, 3.9, 2.1, 1.3, 2, "control-plane/runtime/model-router.ts"),
    node("provider-adapter", "Provider adapter", "provider runtime", "tower", 15.8, 3.6, 2.5, 2, 4, "providers/provider-factory.ts"),
    node("retry-wrapper", "Retry wrapper", "provider runtime", "rack", 19.4, 3.6, 2.2, 1.4, 3, "providers/runtime-bridge.ts"),
    node("auth-endpoint", "Auth + endpoint", "provider runtime", "slab", 15.8, 7.2, 2.6, 1.3, 2, "providers/provider-config-resolution.ts"),
    node("transport", "Provider transport", "provider runtime", "vault", 19.5, 7, 2.2, 2, 3, "providers/provider-factory.ts"),
    node("selected-model", "Selected model", "model execution", "vault", 8.1, 9, 2.5, 2, 3, "types/index.ts"),
    node("reasoning-loop", "Reasoning loop", "model execution", "tower", 11.2, 8.9, 2.1, 2, 4, "providers/thinking.ts"),
    node("completion", "Completion", "model execution", "rack", 8.2, 12.2, 2.5, 1.4, 2, "types/index.ts"),
    node("tool-turns", "Tool turns", "model execution", "slab", 11.3, 12, 2, 1.3, 2, "providers/provider-factory.ts"),
    node("response", "Response", "response and telemetry", "vault", 16, 12.6, 2.7, 2, 3, "loop/generation-side-effect-coordinator.ts"),
    node("latency-span", "Latency span", "response and telemetry", "tower", 19.8, 12.4, 1.8, 1.8, 2, "loop/event-trace.ts"),
    node("token-meter", "Token meter", "response and telemetry", "rack", 16, 15.3, 2.4, 1.1, 2, "loop/generation-side-effect-coordinator.ts"),
    node("call-ledger", "Call ledger", "response and telemetry", "slab", 19.3, 15.1, 2.2, 1.1, 2, "loop/events.ts"),
    node("failure-classifier", "Failure classifier", "reliability", "tower", 0, 9.8, 2, 2, 3, "providers/runtime-bridge.ts"),
    node("backoff-queue", "Backoff queue", "reliability", "rack", 3.5, 9.9, 2.5, 1.4, 3, "providers/runtime-bridge.ts"),
    node("fallback-chain", "Fallback chain", "reliability", "vault", 0.3, 13.2, 2.7, 2, 3, "control-plane/runtime/model-router.ts"),
    node("default-route", "Default route", "reliability", "slab", 3.9, 13.4, 2.1, 1.3, 2, "control-plane/runtime/model-router.ts"),
  ],
  edges: [
    edge("demand-role", "run-demand", "role-request", "role demand"),
    edge("role-input", "role-request", "input-envelope", "role prompt"),
    edge("context-input", "call-context", "input-envelope", "routing context"),
    edge("input-role-router", "input-envelope", "role-router", "call envelope"),
    edge("table-role-router", "route-table", "role-router", "role policy"),
    edge("role-router-model-router", "role-router", "model-router", "provider class"),
    edge("table-model-router", "route-table", "model-router", "ordered routes"),
    edge("model-router-guardrails", "model-router", "guardrails", "matched route"),
    edge("guardrails-adapter", "guardrails", "provider-adapter", "chosen provider + model"),
    edge("adapter-retry", "provider-adapter", "retry-wrapper", "retry policy"),
    edge("retry-endpoint", "retry-wrapper", "auth-endpoint", "call attempt"),
    edge("endpoint-transport", "auth-endpoint", "transport", "authenticated request"),
    edge("transport-model", "transport", "selected-model", "model request"),
    edge("model-reasoning", "selected-model", "reasoning-loop", "reasoning request"),
    edge("reasoning-tools", "reasoning-loop", "tool-turns", "tool turn"),
    edge("tools-reasoning", "tool-turns", "reasoning-loop", "tool result", true),
    edge("reasoning-completion", "reasoning-loop", "completion", "completion"),
    edge("model-completion", "selected-model", "completion", "direct completion"),
    edge("completion-response", "completion", "response", "provider response"),
    edge("response-latency", "response", "latency-span", "latency"),
    edge("response-tokens", "response", "token-meter", "usage"),
    edge("latency-ledger", "latency-span", "call-ledger", "trace span"),
    edge("tokens-ledger", "token-meter", "call-ledger", "token totals"),
    edge("transport-failure", "transport", "failure-classifier", "provider error", true),
    edge("failure-backoff", "failure-classifier", "backoff-queue", "retryable failure"),
    edge("backoff-retry", "backoff-queue", "retry-wrapper", "next attempt", true),
    edge("failure-fallback", "failure-classifier", "fallback-chain", "fallback reason"),
    edge("guardrails-fallback", "guardrails", "fallback-chain", "guardrail demotion", true),
    edge("fallback-default", "fallback-chain", "default-route", "fallback target"),
    edge("default-adapter", "default-route", "provider-adapter", "default provider", true),
  ],
};

export const SYSTEM_MAP_TOPOLOGIES: Readonly<Record<SystemMapView, SystemMapTopology>> = {
  execution: SYSTEM_MAP_TOPOLOGY,
  context: CONTEXT_LINEAGE_TOPOLOGY,
  activation: RUNTIME_ACTIVATION_TOPOLOGY,
  routing: PROVIDER_ROUTING_TOPOLOGY,
};

export function readSystemMapView(value: string | null | undefined): SystemMapView {
  const normalized = value?.trim().toLowerCase();
  if (normalized === "context" || normalized === "activation" || normalized === "routing") {
    return normalized;
  }
  return "execution";
}

export function projectSystemMapTransfer(
  record: EventStreamRecord,
  view: SystemMapView = "execution",
): SystemMapTransfer | null {
  const route = routeForRecord(record, view);
  if (!route) return null;
  const payload = eventPayload(record);
  const runId = readString(payload.run_id) || readString(payload.runId) || runtimeSessionId(record) || "unscoped";
  const generation = readOptionalInteger(payload.generation);
  const attempt = readOptionalInteger(payload.attempt) ?? readOptionalInteger(payload.retry_attempt);
  const summary = safeSummary(payload);
  const status = transferStatus(record.event, payload);
  const trace = record.trace;
  const traceId = trace?.trace_id
    ?? (generation === undefined ? runId : `${runId}:generation:${generation}`);
  const spanId = trace?.span_id ?? `${traceId}:event:${record.seq}`;
  const durationMs = trace?.duration_ms ?? readDuration(payload);
  return {
    version: 1,
    id: `${runId}:${record.seq}:${route.edgeId}`,
    sourceSequence: record.seq,
    timestamp: record.ts,
    runId,
    ...(generation === undefined ? {} : { generation }),
    ...(attempt === undefined ? {} : { attempt }),
    traceId,
    spanId,
    ...(trace?.parent_span_id ? { parentSpanId: trace.parent_span_id } : {}),
    spanName: trace?.name ?? (runtimeEventType(record) || record.event),
    spanPhase: trace?.phase ?? fallbackSpanPhase(record.event),
    startedAt: trace?.started_at ?? record.ts,
    ...(trace?.ended_at ? { endedAt: trace.ended_at } : {}),
    ...(trace ? { payloadBytes: trace.payload_bytes } : {}),
    event: runtimeEventType(record) || record.event,
    edgeId: route.edgeId,
    from: route.from,
    to: route.to,
    kind: route.kind,
    label: route.label,
    status,
    ...(durationMs === undefined ? {} : { durationMs }),
    summary,
  };
}

export function readSystemMapReplay(
  path: string,
  requestedLimit = SYSTEM_MAP_REPLAY_LIMIT,
  view: SystemMapView = "execution",
): SystemMapTransfer[] {
  if (!existsSync(path)) return [];
  const limit = clampInteger(requestedLimit, 1, SYSTEM_MAP_MAX_REPLAY_LIMIT);
  const text = readReplayTail(path);
  const transfers: SystemMapTransfer[] = [];
  for (const line of text.split("\n")) {
    const record = parseEventStreamRecord(line);
    if (!record) continue;
    const transfer = projectSystemMapTransfer(record, view);
    if (transfer) transfers.push(transfer);
  }
  return transfers.slice(-limit);
}

function node(
  id: string,
  label: string,
  group: string,
  kind: SystemMapBuildingKind,
  x: number,
  y: number,
  width: number,
  depth: number,
  height: number,
  source: string,
): SystemMapNode {
  return { id, label, group, kind, x, y, width, depth, height, source };
}

function district(
  id: string,
  label: string,
  group: string,
  color: SystemMapDistrictColor,
  x: number,
  y: number,
  width: number,
  depth: number,
): SystemMapDistrict {
  return { id, label, group, color, x, y, width, depth };
}

function edge(id: string, from: string, to: string, label: string, feedback = false): SystemMapEdge {
  return { id, from, to, label, ...(feedback ? { feedback: true } : {}) };
}

function routeForRecord(record: EventStreamRecord, view: SystemMapView): {
  edgeId: string;
  from: string;
  to: string;
  kind: string;
  label: string;
} | null {
  if (view === "context") return contextRouteForRecord(record);
  if (view === "activation") return activationRouteForRecord(record);
  if (view === "routing") return providerRoutingRouteForRecord(record);
  return executionRouteForRecord(record);
}

function executionRouteForRecord(record: EventStreamRecord): {
  edgeId: string;
  from: string;
  to: string;
  kind: string;
  label: string;
} | null {
  const payload = eventPayload(record);
  const event = runtimeEventType(record) || record.event;
  if (record.event === "runtime_session_event") return runtimeRoute(event, payload);
  if (event === "run_started") return route("entry-runner", "control", "run start");
  if (event === "generation_started" || event === "startup_verification") {
    return route("runner-knowledge", "context", "generation context");
  }
  if (["role_event", "role_started", "role_completed", "role_failed"].includes(event)) {
    return roleRoute(payload);
  }
  if (isValidationEvent(event)) return route("translator-validation", "validation", "validation");
  if (event === "tournament_started") return route("validation-tournament", "evaluation", "tournament");
  if (event === "tournament_completed") return route("tournament-gate", "evaluation", "scores");
  if (event === "gate_decided") {
    const decision = readString(payload.gate_decision) || readString(payload.decision);
    return decision === "retry" || decision === "rollback"
      ? route("gate-validation", "retry", decision || "retry")
      : route("gate-curation", "decision", decision || "gate decision");
  }
  if (event === "curator_started" || event === "curator_completed" || event === "playbook_pending") {
    return route("curation-persistence", "knowledge", "curated learning");
  }
  if (event === "persistence_started") return route("gate-curation", "persistence", "persist generation");
  if (event === "persistence_completed") {
    return route("curation-persistence", "persistence", "generation stored");
  }
  if (event === "generation_completed") {
    return route("persistence-knowledge", "persistence", "next generation");
  }
  if (event === "run_completed") return route("persistence-events", "persistence", "run output");
  if (event === "run_failed" || event === "run_stopped" || event === "generation_failed") {
    return route("runner-events", "control", event.replaceAll("_", " "));
  }
  return null;
}

function contextRouteForRecord(record: EventStreamRecord): {
  edgeId: string;
  from: string;
  to: string;
  kind: string;
  label: string;
} | null {
  const payload = eventPayload(record);
  const event = runtimeEventType(record) || record.event;
  if (record.event === "runtime_session_event" && event === "compaction") {
    return contextRoute("selection-compaction", "compaction", "semantic compaction");
  }
  if (event === "run_started") return contextRoute("goal-selection", "control", "run objective");
  if (event === "generation_started" || event === "startup_verification") {
    return contextRoute("playbook-selection", "context", "prior learning");
  }
  if (event === "role_started") {
    const role = readString(payload.role).toLowerCase();
    if (role === "competitor") return contextRoute("prompt-competitor", "context", "assembled prompt");
    if (role === "analyst" || role === "coach") {
      return contextRoute("evidence-support", "role", role);
    }
    if (role === "curator") return contextRoute("guard-curator", "role", "curator");
  }
  if (event === "role_completed") {
    const role = readString(payload.role).toLowerCase();
    if (role === "competitor") {
      return contextRoute("competitor-candidate", "role", "strategy candidate");
    }
    if (role === "analyst" || role === "coach") {
      return contextRoute("support-guard", "knowledge", role + " synthesis");
    }
    if (role === "curator") return contextRoute("curator-pending", "knowledge", "curated update");
  }
  if (event === "role_failed") {
    const role = readString(payload.role).toLowerCase();
    if (role === "competitor") return contextRoute("prompt-competitor", "role", "competitor failed");
    if (role === "analyst" || role === "coach") {
      return contextRoute("evidence-support", "role", role + " failed");
    }
    if (role === "curator") return contextRoute("guard-curator", "role", "curator failed");
  }
  if (event === "tournament_started") {
    return contextRoute("candidate-tournament", "evaluation", "candidate evaluation");
  }
  if (event === "tournament_completed") {
    return contextRoute("tournament-evidence", "evaluation", "scores + outcomes");
  }
  if (event === "gate_decided") {
    return contextRoute("evidence-gate", "decision", readString(payload.gate_decision) || "gate decision");
  }
  if (event === "dead_end_recorded") {
    return contextRoute("gate-dead-ends", "retry", "rollback evidence");
  }
  if (event === "fresh_start") {
    return contextRoute("dead-ends-selection", "context", "fresh-start evidence");
  }
  if (event === "playbook_update_skipped") {
    return contextRoute("support-guard", "decision", "update rejected");
  }
  if (event === "curator_started") return contextRoute("guard-curator", "role", "curator");
  if (event === "curator_completed") {
    return contextRoute("curator-pending", "decision", readString(payload.decision) || "curator decision");
  }
  if (event === "playbook_pending") {
    return contextRoute("curator-pending", "knowledge", "awaiting approval");
  }
  if (event === "persistence_started" || event === "persistence_completed") {
    return contextRoute("gate-artifacts", "persistence", "generation record");
  }
  if (event === "generation_completed") {
    return contextRoute("durable-next-generation", "knowledge", "retained knowledge");
  }
  if (event === "run_completed") {
    return contextRoute("artifacts-next-generation", "persistence", "run output");
  }
  if (event === "run_failed" || event === "run_stopped" || event === "generation_failed") {
    return contextRoute("gate-artifacts", "control", event.replaceAll("_", " "));
  }
  return null;
}

function activationRouteForRecord(record: EventStreamRecord): {
  edgeId: string;
  from: string;
  to: string;
  kind: string;
  label: string;
} | null {
  if (record.event !== "runtime_session_event") return null;
  const payload = eventPayload(record);
  const event = runtimeEventType(record);
  if (event === "runtime_activation") return runtimeActivationRoute(payload);
  if (event === "component_graph") return componentGraphRoute(payload);
  if (event === "component_lifecycle") return componentLifecycleRoute(payload);
  return null;
}

function runtimeActivationRoute(payload: Record<string, unknown>) {
  const operation = readString(payload.operation).toLowerCase();
  const stage = readString(payload.stage).toLowerCase();
  const outcome = readString(payload.outcome).toLowerCase();
  const failed = outcome === "failed" || outcome === "diverged" || Boolean(readString(payload.failureCode));
  if (failed && stage !== "restored") {
    if (stage.includes("validat")) return activationRoute("validation-failure", "failure", activationLabel(operation, stage));
    if (stage.includes("cutover") || stage === "disposing_prior") {
      return activationRoute("cutover-failure", "failure", activationLabel(operation, stage));
    }
    return activationRoute("recovery-failure", "failure", activationLabel(operation, stage));
  }
  if (stage === "staged") return activationRoute("policy-journal", "transaction", activationLabel(operation, stage));
  if (stage === "applying" || stage === "applied") {
    return activationRoute("ledger-driver", "activation", activationLabel(operation, stage));
  }
  if (stage === "validating") return activationRoute("scopes-validation", "validation", activationLabel(operation, stage));
  if (stage === "validated") return activationRoute("validation-observed", "validation", activationLabel(operation, stage));
  if (stage === "activating" || stage === "activated") {
    return activationRoute("bindings-scopes", "lifecycle", activationLabel(operation, stage));
  }
  if (stage === "draining" || stage === "drained") {
    return activationRoute("observed-drain", "cutover", activationLabel(operation, stage));
  }
  if (stage === "cutting_over") return activationRoute("drain-cutover", "cutover", activationLabel(operation, stage));
  if (stage === "runtime_cutover") return activationRoute("cutover-live", "cutover", activationLabel(operation, stage));
  if (stage === "pointer_cutover") return activationRoute("cutover-pointer", "cutover", activationLabel(operation, stage));
  if (stage === "disposing_prior") return activationRoute("live-disposal", "lifecycle", activationLabel(operation, stage));
  if (stage === "committed") return activationRoute("disposal-registry", "commit", activationLabel(operation, stage));
  if (stage === "reverting") return activationRoute("failure-unwind", "rollback", activationLabel(operation, stage));
  if (stage === "restored") return activationRoute("unwind-restore", "rollback", activationLabel(operation, stage));
  if (operation === "repair") return activationRoute("pointer-recovery", "repair", activationLabel(operation, stage));
  return null;
}

function componentGraphRoute(payload: Record<string, unknown>) {
  const operation = readString(payload.operation).toLowerCase();
  const label = operation.replaceAll("_", " ") || "component graph";
  switch (operation) {
    case "provider_unavailable": return activationRoute("bindings-failure", "dependency", label);
    case "component_waiting": return activationRoute("dependencies-bindings", "dependency", label);
    case "component_activated": return activationRoute("bindings-scopes", "lifecycle", label);
    case "component_deactivated": return activationRoute("live-disposal", "lifecycle", label);
    case "component_failed": return activationRoute("scopes-failure", "failure", label);
    case "graph_reconciled": return activationRoute("driver-dependencies", "reconcile", label);
    default: return null;
  }
}

function componentLifecycleRoute(payload: Record<string, unknown>) {
  const operation = readString(payload.operation).toLowerCase();
  const outcome = readString(payload.outcome).toLowerCase();
  const state = readString(payload.state).toLowerCase();
  const label = [operation, state].filter(Boolean).join(" · ") || "component lifecycle";
  if (outcome === "failed" || state === "failed") {
    return activationRoute("scopes-failure", "failure", label);
  }
  if (operation === "activate") {
    return state === "active"
      ? activationRoute("scopes-validation", "lifecycle", label)
      : activationRoute("bindings-scopes", "lifecycle", label);
  }
  if (operation === "unwind") return activationRoute("failure-unwind", "rollback", label);
  if (operation === "dispose") {
    return state === "inactive"
      ? activationRoute("disposal-registry", "lifecycle", label)
      : activationRoute("live-disposal", "lifecycle", label);
  }
  return null;
}

function activationLabel(operation: string, stage: string): string {
  return [operation, stage.replaceAll("_", " ")].filter(Boolean).join(" · ") || "runtime activation";
}

function providerRoutingRouteForRecord(record: EventStreamRecord): {
  edgeId: string;
  from: string;
  to: string;
  kind: string;
  label: string;
} | null {
  const payload = eventPayload(record);
  const event = runtimeEventType(record) || record.event;
  if (event === "run_started") return providerRoutingRoute("demand-role", "control", "run demand");
  if (event === "generation_started") {
    return providerRoutingRoute("input-role-router", "routing", "generation call context");
  }
  if (event === "role_started") {
    const attempt = readOptionalInteger(payload.attempt) ?? 1;
    if (attempt > 1) {
      return providerRoutingRoute("backoff-retry", "retry", providerCallLabel(payload, "retry call"));
    }
    const routingReason = readString(payload.routingReason) || readString(payload.reason);
    if (routingReason.toLowerCase() === "fallback") {
      return providerRoutingRoute("guardrails-fallback", "fallback", providerCallLabel(payload, "fallback"));
    }
    return providerRoutingRoute("guardrails-adapter", "routing", providerCallLabel(payload, "selected route"));
  }
  if (event === "role_completed") {
    return providerRoutingRoute("completion-response", "completion", providerCallLabel(payload, "response"));
  }
  if (event === "role_failed") {
    return providerRoutingRoute("transport-failure", "failure", providerCallLabel(payload, "provider failure"));
  }
  if (event === "model_routing_decided") {
    const reason = (readString(payload.routingReason) || readString(payload.reason)).toLowerCase();
    return reason === "fallback"
      ? providerRoutingRoute("guardrails-fallback", "fallback", providerCallLabel(payload, "fallback"))
      : providerRoutingRoute("model-router-guardrails", "routing", providerCallLabel(payload, reason || "route decision"));
  }
  if (event === "provider_call_started") {
    return providerRoutingRoute("endpoint-transport", "provider", providerCallLabel(payload, "provider request"));
  }
  if (event === "provider_call_completed") {
    return providerRoutingRoute("model-completion", "completion", providerCallLabel(payload, "model completion"));
  }
  if (event === "provider_call_failed") {
    return providerRoutingRoute("transport-failure", "failure", providerCallLabel(payload, "provider failure"));
  }
  if (event === "provider_retry") {
    const status = readString(payload.status).toLowerCase();
    return status === "scheduled" || status === "waiting"
      ? providerRoutingRoute("failure-backoff", "retry", providerCallLabel(payload, "backoff"))
      : providerRoutingRoute("backoff-retry", "retry", providerCallLabel(payload, "retry attempt"));
  }
  return null;
}

function providerCallLabel(payload: Record<string, unknown>, fallback: string): string {
  const role = readString(payload.role).toLowerCase();
  const provider = readString(payload.provider);
  const model = readString(payload.model);
  const target = [provider, model].filter(Boolean).join(" / ");
  return [role, target].filter(Boolean).join(" · ") || fallback;
}

function runtimeRoute(event: string, payload: Record<string, unknown>) {
  if (event === "component_graph" || event === "component_lifecycle" || event === "runtime_activation") {
    return route("runtime-runner", "lifecycle", readString(payload.operation) || event.replaceAll("_", " "));
  }
  if (event === "prompt_submitted" || event === "assistant_message") return roleRoute(payload);
  if (event === "child_task_started" || event === "child_task_completed") {
    return route("runner-events", "task", event.replaceAll("_", " "));
  }
  if (event === "tool_call" || event === "shell_command" || event === "compaction") {
    return route("runner-events", "runtime", event.replaceAll("_", " "));
  }
  return null;
}

function roleRoute(payload: Record<string, unknown>) {
  const role = readString(payload.role).toLowerCase();
  switch (role) {
    case "competitor": return route("knowledge-competitor", "role", "competitor");
    case "translator": return route("competitor-translator", "role", "translator");
    case "analyst": return route("translator-analyst", "role", "analyst");
    case "architect": return route("translator-architect", "role", "architect");
    case "coach": return route("analyst-coach", "role", "coach");
    default: return route("runner-events", "role", role || "role event");
  }
}

function route(edgeId: string, kind: string, label: string) {
  const edgeRecord = SYSTEM_MAP_TOPOLOGY.edges.find((candidate) => candidate.id === edgeId);
  if (!edgeRecord) throw new Error(`Unknown system-map edge: ${edgeId}`);
  return { edgeId, from: edgeRecord.from, to: edgeRecord.to, kind, label };
}

function contextRoute(edgeId: string, kind: string, label: string) {
  const edgeRecord = CONTEXT_LINEAGE_TOPOLOGY.edges.find((candidate) => candidate.id === edgeId);
  if (!edgeRecord) throw new Error(`Unknown context-lineage edge: ${edgeId}`);
  return { edgeId, from: edgeRecord.from, to: edgeRecord.to, kind, label };
}

function activationRoute(edgeId: string, kind: string, label: string) {
  const edgeRecord = RUNTIME_ACTIVATION_TOPOLOGY.edges.find((candidate) => candidate.id === edgeId);
  if (!edgeRecord) throw new Error(`Unknown runtime-activation edge: ${edgeId}`);
  return { edgeId, from: edgeRecord.from, to: edgeRecord.to, kind, label };
}

function providerRoutingRoute(edgeId: string, kind: string, label: string) {
  const edgeRecord = PROVIDER_ROUTING_TOPOLOGY.edges.find((candidate) => candidate.id === edgeId);
  if (!edgeRecord) throw new Error(`Unknown provider-routing edge: ${edgeId}`);
  return { edgeId, from: edgeRecord.from, to: edgeRecord.to, kind, label };
}

function eventPayload(record: EventStreamRecord): Record<string, unknown> {
  if (record.event !== "runtime_session_event") return record.payload;
  const event = readRecord(record.payload.event);
  return readRecord(event.payload);
}

function runtimeEventType(record: EventStreamRecord): string {
  if (record.event !== "runtime_session_event") return "";
  return readString(readRecord(record.payload.event).event_type);
}

function runtimeSessionId(record: EventStreamRecord): string {
  return readString(record.payload.session_id);
}

function isValidationEvent(event: string): boolean {
  return event.startsWith("staged_validation_")
    || event.startsWith("harness_validation_")
    || event.startsWith("dry_run_")
    || event.startsWith("probe_")
    || event.startsWith("regression_fixtures_");
}

const SUMMARY_KEYS = [
  "role",
  "status",
  "phase",
  "generation",
  "attempt",
  "retry_attempt",
  "gate_decision",
  "decision",
  "operation",
  "outcome",
  "stage",
  "failureCode",
  "transactionId",
  "candidateArtifactId",
  "priorArtifactId",
  "componentId",
  "instanceId",
  "capabilityId",
  "providerComponentId",
  "providerInstanceId",
  "previousState",
  "state",
  "reason",
  "latency_ms",
  "durationMs",
  "tokens",
  "input_bytes",
  "output_bytes",
  "provider",
  "model",
  "routingReason",
  "matchedRouteId",
  "fallbackReason",
  "endpoint",
  "budgetRemainingUsd",
  "latencyBudgetMs",
  "confidenceScore",
  "retryAfterMs",
  "mean_score",
  "best_score",
  "revision",
  "scenario",
  "score",
  "trigger",
  "entryCount",
  "components",
  "tokensBefore",
  "source",
] as const;

function safeSummary(payload: Record<string, unknown>): Record<string, string | number | boolean> {
  const summary: Record<string, string | number | boolean> = {};
  for (const key of SUMMARY_KEYS) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) {
      summary[key] = value.slice(0, MAX_SUMMARY_VALUE_LENGTH);
    } else if (typeof value === "number" && Number.isFinite(value)) {
      summary[key] = value;
    } else if (typeof value === "boolean") {
      summary[key] = value;
    }
  }
  return summary;
}

function transferStatus(event: string, payload: Record<string, unknown>): SystemMapTransferStatus {
  const status = readString(payload.status).toLowerCase();
  const outcome = readString(payload.outcome).toLowerCase();
  const operation = readString(payload.operation).toLowerCase();
  const stage = readString(payload.stage).toLowerCase();
  const decision = (readString(payload.gate_decision) || readString(payload.decision)).toLowerCase();
  if (
    event.includes("failed") || event.endsWith("_skipped")
    || [status, outcome].some((value) => ["diverged", "error", "failed", "failure"].includes(value))
  ) {
    return "failed";
  }
  if (
    decision === "retry" || decision === "rollback" || outcome === "waiting"
    || operation === "rollback" || operation === "repair" || stage === "reverting"
  ) return "retry";
  if (
    event.endsWith("_started") || status === "started" || status === "running"
    || outcome === "started" || outcome === "in_progress"
  ) return "started";
  return "completed";
}

function readDuration(payload: Record<string, unknown>): number | undefined {
  const value = payload.duration_ms ?? payload.durationMs ?? payload.latency_ms;
  return typeof value === "number" && Number.isFinite(value)
    ? clampInteger(value, 0, 120_000)
    : undefined;
}

function fallbackSpanPhase(event: string): "start" | "complete" | "instant" {
  if (event.endsWith("_started")) return "start";
  if (event.endsWith("_completed") || event.endsWith("_failed") || event.endsWith("_stopped")) {
    return "complete";
  }
  return "instant";
}

function parseEventStreamRecord(line: string): EventStreamRecord | null {
  const trimmed = line.trim();
  if (!trimmed) return null;
  try {
    const value = JSON.parse(trimmed) as unknown;
    const record = readRecord(value);
    const payload = readRecord(record.payload);
    const event = readString(record.event);
    const channel = readString(record.channel);
    const ts = readString(record.ts);
    const seq = readOptionalInteger(record.seq);
    if (!event || !channel || !ts || seq === undefined) return null;
    const trace = readEventTraceSpan(record.trace);
    return { channel, event, payload, seq, ts, v: 1, ...(trace ? { trace } : {}) };
  } catch {
    return null;
  }
}

function readReplayTail(path: string): string {
  const fd = openSync(path, "r");
  try {
    const size = fstatSync(fd).size;
    if (size <= SYSTEM_MAP_MAX_REPLAY_BYTES) return readFileSync(fd, "utf-8");
    const offset = size - SYSTEM_MAP_MAX_REPLAY_BYTES;
    const buffer = Buffer.allocUnsafe(SYSTEM_MAP_MAX_REPLAY_BYTES);
    const bytesRead = readSync(fd, buffer, 0, buffer.length, offset);
    const tail = buffer.subarray(0, bytesRead).toString("utf-8");
    const firstNewline = tail.indexOf("\n");
    return firstNewline === -1 ? "" : tail.slice(firstNewline + 1);
  } finally {
    closeSync(fd);
  }
}

function readRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function readString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function readOptionalInteger(value: unknown): number | undefined {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : undefined;
}

function clampInteger(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, Math.trunc(value)));
}
