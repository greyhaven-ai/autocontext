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

import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { LLMProvider } from "../types/index.js";
import { generateScenarioSource } from "../scenarios/codegen/index.js";
import { validateGeneratedScenario } from "../scenarios/codegen/execution-validator.js";
import { healSpec } from "../scenarios/spec-auto-heal.js";
import { getScenarioTypeMarker } from "../scenarios/families.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface InvestigationRequest {
  description: string;
  maxSteps?: number;
  maxHypotheses?: number;
  saveAs?: string;
  strictEvidence?: boolean;
}

export interface Hypothesis {
  id: string;
  statement: string;
  status: "supported" | "contradicted" | "unresolved";
  confidence: number;
}

export interface Evidence {
  id: string;
  kind: string;
  source: string;
  summary: string;
  supports: string[];
  contradicts: string[];
  isRedHerring: boolean;
}

export interface Conclusion {
  bestExplanation: string;
  confidence: number;
  limitations: string[];
}

export interface InvestigationResult {
  id: string;
  name: string;
  family: "investigation";
  status: "completed" | "failed";
  description: string;
  question: string;
  hypotheses: Hypothesis[];
  evidence: Evidence[];
  conclusion: Conclusion;
  unknowns: string[];
  recommendedNextSteps: string[];
  stepsExecuted: number;
  artifacts: {
    investigationDir: string;
    reportPath?: string;
  };
  error?: string;
}

// ---------------------------------------------------------------------------
// Engine
// ---------------------------------------------------------------------------

function generateId(): string {
  return `inv_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

export class InvestigationEngine {
  private provider: LLMProvider;
  private knowledgeRoot: string;

  constructor(provider: LLMProvider, knowledgeRoot: string) {
    this.provider = provider;
    this.knowledgeRoot = knowledgeRoot;
  }

  async run(request: InvestigationRequest): Promise<InvestigationResult> {
    const id = generateId();
    const name = request.saveAs ?? this.deriveName(request.description);

    try {
      // Step 1: Build investigation spec via LLM
      const spec = await this.buildSpec(request.description);
      const healedSpec = healSpec(spec, "investigation");

      // Step 2: Generate + validate investigation scenario code
      const source = generateScenarioSource("investigation", healedSpec, name);
      const validation = await validateGeneratedScenario(source, "investigation", name);
      if (!validation.valid) {
        return this.failedResult(id, name, request, validation.errors);
      }

      // Step 3: Persist artifacts
      const investigationDir = this.persistArtifacts(name, healedSpec, source);

      // Step 4: Execute the investigation scenario
      const execution = await this.executeInvestigation(source, name, request.maxSteps);

      // Step 5: Generate hypotheses via LLM
      const hypothesisData = await this.generateHypotheses(request.description, execution);

      // Step 6: Build evidence from execution + spec
      const evidence = this.buildEvidence(healedSpec, execution);

      // Step 7: Evaluate hypotheses against evidence
      const hypotheses = this.evaluateHypotheses(hypothesisData, evidence);

      // Step 8: Build conclusion
      const conclusion = this.buildConclusion(hypotheses, evidence);
      const unknowns = this.identifyUnknowns(hypotheses, evidence);
      const nextSteps = this.recommendNextSteps(hypotheses, unknowns);

      // Step 9: Save report
      const reportPath = join(investigationDir, "report.json");
      const result: InvestigationResult = {
        id, name,
        family: "investigation",
        status: "completed",
        description: request.description,
        question: String(hypothesisData.question ?? `What caused: ${request.description}`),
        hypotheses,
        evidence,
        conclusion,
        unknowns,
        recommendedNextSteps: nextSteps,
        stepsExecuted: execution.stepsExecuted,
        artifacts: { investigationDir, reportPath },
      };
      writeFileSync(reportPath, JSON.stringify(result, null, 2), "utf-8");

      return result;
    } catch (err) {
      return this.failedResult(id, name, request,
        [err instanceof Error ? err.message : String(err)]);
    }
  }

  // -------------------------------------------------------------------------
  // Internals
  // -------------------------------------------------------------------------

  private async buildSpec(description: string): Promise<Record<string, unknown>> {
    const result = await this.provider.complete({
      systemPrompt: `You are an investigation designer. Given a problem description, produce an investigation spec as JSON.

Required fields:
- description: investigation summary
- environment_description: system/context being investigated
- initial_state_description: what is known at the start
- evidence_pool_description: what evidence sources are available
- diagnosis_target: what we're trying to determine
- success_criteria: array of strings (what constitutes a successful investigation)
- failure_modes: array of strings
- max_steps: positive integer
- actions: array of {name, description, parameters, preconditions, effects}
- evidence_pool: array of {id, content, isRedHerring, relevance}
- correct_diagnosis: the ground truth answer

Output ONLY the JSON object, no markdown fences.`,
      userPrompt: `Investigation: ${description}`,
    });

    return this.parseJSON(result.text) ?? {
      description,
      environment_description: "System under investigation",
      initial_state_description: "Anomaly detected",
      evidence_pool_description: "Available system data",
      diagnosis_target: description,
      success_criteria: ["identify root cause"],
      failure_modes: ["inconclusive"],
      max_steps: 8,
      actions: [
        { name: "gather_evidence", description: "Collect evidence", parameters: {}, preconditions: [], effects: ["evidence_gathered"] },
      ],
      evidence_pool: [
        { id: "initial_observation", content: description, isRedHerring: false, relevance: 0.5 },
      ],
      correct_diagnosis: "unknown",
    };
  }

  private async generateHypotheses(
    description: string,
    execution: { stepsExecuted: number; collectedEvidence: string[] },
  ): Promise<{ hypotheses: Array<{ statement: string; confidence: number }>; question: string }> {
    try {
      const result = await this.provider.complete({
        systemPrompt: `You are a diagnostic analyst. Given an investigation description and collected evidence, generate hypotheses. Output JSON:
{
  "question": "The specific question being investigated",
  "hypotheses": [
    { "statement": "Hypothesis text", "confidence": 0.0-1.0 }
  ]
}
Output ONLY the JSON object.`,
        userPrompt: `Investigation: ${description}\nEvidence collected: ${execution.collectedEvidence.join(", ") || "none yet"}\nSteps taken: ${execution.stepsExecuted}`,
      });

      const parsed = this.parseJSON(result.text);
      if (parsed?.hypotheses && Array.isArray(parsed.hypotheses)) {
        return {
          question: String(parsed.question ?? description),
          hypotheses: (parsed.hypotheses as Array<Record<string, unknown>>)
            .filter((h) => typeof h.statement === "string")
            .map((h) => ({
              statement: String(h.statement),
              confidence: typeof h.confidence === "number" ? Math.min(1, Math.max(0, h.confidence)) : 0.5,
            })),
        };
      }
    } catch { /* fallback */ }

    return {
      question: description,
      hypotheses: [{ statement: `Investigate: ${description}`, confidence: 0.5 }],
    };
  }

  private async executeInvestigation(
    source: string, name: string, maxSteps?: number,
  ): Promise<{ stepsExecuted: number; collectedEvidence: string[]; finalState: Record<string, unknown> }> {
    const moduleObj = { exports: {} as Record<string, unknown> };
    const fn = new Function("module", "exports", source);
    fn(moduleObj, moduleObj.exports);
    const scenario = (moduleObj.exports as { scenario: Record<string, (...args: unknown[]) => unknown> }).scenario;

    let state = scenario.initialState(42) as Record<string, unknown>;
    const limit = maxSteps ?? 8;
    let steps = 0;

    while (steps < limit) {
      const terminal = scenario.isTerminal(state) as boolean;
      if (terminal) break;
      const actions = scenario.getAvailableActions(state) as Array<{ name: string }>;
      if (!actions || actions.length === 0) break;
      const actionResult = scenario.executeAction(state, { name: actions[0].name, parameters: {} }) as {
        result: Record<string, unknown>; state: Record<string, unknown>;
      };
      state = actionResult.state;
      steps++;
    }

    const collectedEvidence = ((state.collectedEvidence ?? []) as Array<{ id?: string; content?: string }>)
      .map((e) => e.content ?? e.id ?? "unknown");

    return { stepsExecuted: steps, collectedEvidence, finalState: state };
  }

  private buildEvidence(
    spec: Record<string, unknown>,
    execution: { collectedEvidence: string[] },
  ): Evidence[] {
    const pool = (spec.evidence_pool ?? spec.evidencePool ?? []) as Array<{
      id: string; content: string; isRedHerring?: boolean; relevance: number;
    }>;

    return pool.map((e, i) => ({
      id: e.id ?? `e${i}`,
      kind: e.isRedHerring ? "red_herring" : "observation",
      source: "scenario evidence pool",
      summary: e.content,
      supports: e.isRedHerring ? [] : ["h0"],
      contradicts: e.isRedHerring ? ["h0"] : [],
      isRedHerring: !!e.isRedHerring,
    }));
  }

  private evaluateHypotheses(
    hypothesisData: { hypotheses: Array<{ statement: string; confidence: number }> },
    evidence: Evidence[],
  ): Hypothesis[] {
    return hypothesisData.hypotheses.map((h, i) => {
      const id = `h${i}`;
      const supporting = evidence.filter((e) => e.supports.includes(id) && !e.isRedHerring);
      const contradicting = evidence.filter((e) => e.contradicts.includes(id));

      let status: Hypothesis["status"] = "unresolved";
      if (supporting.length > contradicting.length && h.confidence >= 0.5) {
        status = "supported";
      } else if (contradicting.length > supporting.length) {
        status = "contradicted";
      }

      return { id, statement: h.statement, status, confidence: h.confidence };
    });
  }

  private buildConclusion(hypotheses: Hypothesis[], evidence: Evidence[]): Conclusion {
    const best = hypotheses
      .filter((h) => h.status === "supported")
      .sort((a, b) => b.confidence - a.confidence)[0];

    const redHerrings = evidence.filter((e) => e.isRedHerring).length;
    const limitations: string[] = [];
    if (redHerrings > 0) limitations.push(`${redHerrings} potential red herring(s) in evidence pool`);
    if (hypotheses.filter((h) => h.status === "unresolved").length > 0) {
      limitations.push("Some hypotheses remain unresolved");
    }
    limitations.push("Investigation based on generated scenario — not live system data");

    return {
      bestExplanation: best?.statement ?? "No hypothesis received sufficient support",
      confidence: best?.confidence ?? 0,
      limitations,
    };
  }

  private identifyUnknowns(hypotheses: Hypothesis[], evidence: Evidence[]): string[] {
    const unknowns: string[] = [];
    const unresolved = hypotheses.filter((h) => h.status === "unresolved");
    for (const h of unresolved) {
      unknowns.push(`Hypothesis "${h.statement}" needs more evidence`);
    }
    if (evidence.length < 3) {
      unknowns.push("Limited evidence collected — more data sources needed");
    }
    return unknowns;
  }

  private recommendNextSteps(hypotheses: Hypothesis[], unknowns: string[]): string[] {
    const steps: string[] = [];
    const supported = hypotheses.filter((h) => h.status === "supported");
    if (supported.length > 0) {
      steps.push(`Verify leading hypothesis: "${supported[0].statement}"`);
    }
    const unresolved = hypotheses.filter((h) => h.status === "unresolved");
    for (const h of unresolved.slice(0, 2)) {
      steps.push(`Gather evidence for: "${h.statement}"`);
    }
    if (unknowns.length > 0) {
      steps.push("Address identified unknowns before concluding");
    }
    return steps;
  }

  private persistArtifacts(
    name: string, spec: Record<string, unknown>, source: string,
  ): string {
    const investigationDir = join(this.knowledgeRoot, "_investigations", name);
    if (!existsSync(investigationDir)) mkdirSync(investigationDir, { recursive: true });
    writeFileSync(join(investigationDir, "spec.json"), JSON.stringify({ name, family: "investigation", ...spec }, null, 2), "utf-8");
    writeFileSync(join(investigationDir, "scenario.js"), source, "utf-8");
    writeFileSync(join(investigationDir, "scenario_type.txt"), getScenarioTypeMarker("investigation"), "utf-8");
    return investigationDir;
  }

  private failedResult(
    id: string, name: string, request: InvestigationRequest, errors: string[],
  ): InvestigationResult {
    return {
      id, name, family: "investigation", status: "failed",
      description: request.description,
      question: request.description,
      hypotheses: [], evidence: [],
      conclusion: { bestExplanation: "", confidence: 0, limitations: errors },
      unknowns: [], recommendedNextSteps: [],
      stepsExecuted: 0,
      artifacts: { investigationDir: "" },
      error: errors.join("; "),
    };
  }

  private deriveName(description: string): string {
    return description.toLowerCase().replace(/[^a-z0-9\s]/g, "").split(/\s+/)
      .filter((w) => w.length > 2).slice(0, 4).join("_") || "investigation";
  }

  private parseJSON(text: string): Record<string, unknown> | null {
    const trimmed = text.trim();
    try { return JSON.parse(trimmed); } catch { /* continue */ }
    const start = trimmed.indexOf("{");
    const end = trimmed.lastIndexOf("}");
    if (start !== -1 && end > start) {
      try { return JSON.parse(trimmed.slice(start, end + 1)); } catch { /* continue */ }
    }
    return null;
  }
}
