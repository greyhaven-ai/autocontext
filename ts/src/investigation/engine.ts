/**
 * Investigation engine — first-class `investigate` surface (AC-447).
 *
 * Takes a plain-language problem description, builds an investigation spec
 * via LLM, gathers evidence, evaluates hypotheses, and returns structured
 * findings with confidence, uncertainty, and recommended next steps.
 *
 * Built on top of the existing investigation family codegen and the
 * same materialization/execution patterns used by simulate.
 */

import type { LLMProvider } from "../types/index.js";
import {
  deriveInvestigationName,
  generateInvestigationId,
} from "./investigation-engine-helpers.js";
import { executeInvestigationRun } from "./investigation-run-workflow.js";

export type {
  Conclusion,
  Evidence,
  Hypothesis,
  InvestigationRequest,
  InvestigationResult,
} from "./investigation-contracts.js";
import type { InvestigationRequest, InvestigationResult } from "./investigation-contracts.js";

export class InvestigationEngine {
  #provider: LLMProvider;
  #knowledgeRoot: string;

  constructor(provider: LLMProvider, knowledgeRoot: string) {
    this.#provider = provider;
    this.#knowledgeRoot = knowledgeRoot;
  }

  async run(request: InvestigationRequest): Promise<InvestigationResult> {
    return executeInvestigationRun({
      id: generateInvestigationId(),
      name: request.saveAs ?? deriveInvestigationName(request.description),
      request,
      provider: this.#provider,
      knowledgeRoot: this.#knowledgeRoot,
    });
  }
}
