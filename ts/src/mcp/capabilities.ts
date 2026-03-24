/**
 * Capability discovery — return metadata about this autocontext instance (AC-370).
 * Mirrors Python's autocontext/mcp/tools.py::get_capabilities.
 */

import { createRequire } from "node:module";
import { SCENARIO_REGISTRY } from "../scenarios/registry.js";

const require = createRequire(import.meta.url);
const pkg = require("../../package.json") as { version: string };

export interface Capabilities {
  version: string;
  scenarios: string[];
  providers: string[];
  features: string[];
  pythonOnly: string[];
}

export function getCapabilities(): Capabilities {
  return {
    version: pkg.version,
    scenarios: Object.keys(SCENARIO_REGISTRY).sort(),
    providers: [
      "anthropic",
      "openai",
      "openai-compatible",
      "ollama",
      "vllm",
      "hermes",
      "pi",
      "pi-rpc",
      "deterministic",
    ],
    features: [
      "generation_loop",
      "tournament",
      "backpressure_gate",
      "playbook_versioning",
      "score_trajectory",
      "context_budget",
      "mcp_server",
      "interactive_server",
      "training_data_export",
      "custom_scenarios",
      "human_feedback",
      "session_reports",
      "dead_end_tracking",
      "stagnation_detection",
    ],
    pythonOnly: [
      "train",
      "ecosystem",
      "ab-test",
      "resume",
      "wait",
      "trigger-distillation",
      "monitor-conditions",
      "mlx-inference",
      "ssh-executor",
      "monty-sandbox",
    ],
  };
}
