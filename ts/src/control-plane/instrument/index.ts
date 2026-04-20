/**
 * Public barrel for A2-I `autoctx instrument` tool infrastructure.
 *
 * Layers 1 + 2 only — contract + scanner. Additional layers (safety, registry,
 * planner, pipeline, llm, cli) land in follow-up commits per spec §11.6.
 */
export * from "./contract/index.js";
export * from "./scanner/index.js";
