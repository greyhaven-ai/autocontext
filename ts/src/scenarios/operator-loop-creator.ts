import type { LLMProvider } from "../types/index.js";
import type { OperatorLoopSpec } from "./operator-loop-spec.js";

export interface OperatorLoopCreatorOpts {
  provider: LLMProvider;
  model?: string;
  knowledgeRoot: string;
}

export interface OperatorLoopScenarioHandle {
  family: "operator_loop";
  name: string;
  spec: OperatorLoopSpec;
}

export const OPERATOR_LOOP_SCAFFOLDING_UNSUPPORTED =
  "operator_loop scenarios are intentionally not scaffolded into harness-owned executable runtimes; use family metadata, datasets, tools, or live-agent experiments instead";

export class OperatorLoopCreator {
  private provider: LLMProvider;
  private model: string;
  private knowledgeRoot: string;

  constructor(opts: OperatorLoopCreatorOpts) {
    this.provider = opts.provider;
    this.model = opts.model ?? opts.provider.defaultModel();
    this.knowledgeRoot = opts.knowledgeRoot;
  }

  async create(description: string, name: string): Promise<OperatorLoopScenarioHandle> {
    void description;
    void name;
    void this.provider;
    void this.model;
    void this.knowledgeRoot;
    throw new Error(OPERATOR_LOOP_SCAFFOLDING_UNSUPPORTED);
  }
}
