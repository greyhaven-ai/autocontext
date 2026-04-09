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
import { generateScenarioSource } from "../scenarios/codegen/registry.js";
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

interface CollectedEvidenceItem {
  id: string;
  content: string;
  isRedHerring: boolean;
  relevance: number;
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
      const hypothesisData = await this.generateHypotheses(
        request.description,
        execution,
        request.maxHypotheses,
      );

      // Step 6: Build evidence from execution + spec
      const evidence = this.buildEvidence(execution);

      // Step 7: Evaluate hypotheses against evidence
      const { evidence: annotatedEvidence, hypotheses } = this.evaluateHypotheses(
        hypothesisData,
        evidence,
        healedSpec,
      );

      // Step 8: Build conclusion
      const conclusion = this.buildConclusion(hypotheses, annotatedEvidence);
      const unknowns = this.identifyUnknowns(hypotheses, annotatedEvidence);
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
        evidence: annotatedEvidence,
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

    const parsed = this.parseJSON(result.text);
    if (!parsed) {
      throw new Error("Investigation spec generation did not return valid JSON");
    }
    return parsed;
  }

  private async generateHypotheses(
    description: string,
    execution: { stepsExecuted: number; collectedEvidence: CollectedEvidenceItem[] },
    maxHypotheses?: number,
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
        userPrompt: `Investigation: ${description}\nEvidence collected: ${
          execution.collectedEvidence.map((item) => item.content).join(", ") || "none yet"
        }\nSteps taken: ${execution.stepsExecuted}\nMaximum hypotheses: ${maxHypotheses ?? 5}`,
      });

      const parsed = this.parseJSON(result.text);
      if (parsed?.hypotheses && Array.isArray(parsed.hypotheses)) {
        const parsedHypotheses = (parsed.hypotheses as Array<Record<string, unknown>>)
          .filter((h) => typeof h.statement === "string")
          .map((h) => ({
            statement: String(h.statement),
            confidence: typeof h.confidence === "number" ? Math.min(1, Math.max(0, h.confidence)) : 0.5,
          }));
        const limit = this.normalizePositiveInteger(maxHypotheses);
        return {
          question: String(parsed.question ?? description),
          hypotheses: typeof limit === "number" ? parsedHypotheses.slice(0, limit) : parsedHypotheses,
        };
      }
    } catch { /* fallback */ }

    return {
      question: description,
      hypotheses: [{ statement: `Investigate: ${description}`, confidence: 0.5 }]
        .slice(0, this.normalizePositiveInteger(maxHypotheses) ?? 1),
    };
  }

  private async executeInvestigation(
    source: string, name: string, maxSteps?: number,
  ): Promise<{ stepsExecuted: number; collectedEvidence: CollectedEvidenceItem[]; finalState: Record<string, unknown> }> {
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

    const collectedEvidence = ((state.collectedEvidence ?? []) as Array<Record<string, unknown>>)
      .map((e, index) => ({
        id: typeof e.id === "string" ? e.id : `collected_${index}`,
        content:
          typeof e.content === "string"
            ? e.content
            : typeof e.summary === "string"
              ? e.summary
              : typeof e.id === "string"
                ? e.id
                : "unknown",
        isRedHerring: !!e.isRedHerring,
        relevance: typeof e.relevance === "number" ? e.relevance : 0,
      }));

    return { stepsExecuted: steps, collectedEvidence, finalState: state };
  }

  private buildEvidence(
    execution: { collectedEvidence: CollectedEvidenceItem[] },
  ): Evidence[] {
    return execution.collectedEvidence.map((e, i) => ({
      id: e.id ?? `e${i}`,
      kind: e.isRedHerring ? "red_herring" : "observation",
      source: "scenario execution",
      summary: e.content,
      supports: [],
      contradicts: [],
      isRedHerring: !!e.isRedHerring,
    }));
  }

  private evaluateHypotheses(
    hypothesisData: { hypotheses: Array<{ statement: string; confidence: number }> },
    evidence: Evidence[],
    spec: Record<string, unknown>,
  ): { evidence: Evidence[]; hypotheses: Hypothesis[] } {
    const annotatedEvidence = evidence.map((item) => ({
      ...item,
      supports: [...item.supports],
      contradicts: [...item.contradicts],
    }));
    const correctDiagnosis = this.normalizeText(
      String(spec.correct_diagnosis ?? spec.correctDiagnosis ?? spec.diagnosis_target ?? spec.diagnosisTarget ?? ""),
    );

    const hypotheses = hypothesisData.hypotheses.map((h, i) => {
      const id = `h${i}`;
      const matchesCorrectDiagnosis =
        correctDiagnosis.length > 0 && this.similarityScore(h.statement, correctDiagnosis) >= 0.34;
      let supporting = 0;
      let contradicting = 0;

      for (const item of annotatedEvidence) {
        const overlap = this.similarityScore(h.statement, item.summary);
        const related = overlap >= 0.34;
        if (item.isRedHerring) {
          if (related) {
            item.contradicts.push(id);
            contradicting += overlap;
          }
        } else if (related || matchesCorrectDiagnosis) {
          item.supports.push(id);
          supporting += Math.max(overlap, matchesCorrectDiagnosis ? 0.5 : 0);
        }
      }

      let status: Hypothesis["status"] = "unresolved";
      if (supporting > contradicting && supporting > 0) {
        status = "supported";
      } else if (contradicting > supporting && contradicting > 0) {
        status = "contradicted";
      }

      return { id, statement: h.statement, status, confidence: h.confidence };
    });

    return { evidence: annotatedEvidence, hypotheses };
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

  private normalizePositiveInteger(value: number | undefined): number | undefined {
    if (typeof value !== "number" || !Number.isFinite(value)) {
      return undefined;
    }
    const rounded = Math.floor(value);
    return rounded > 0 ? rounded : undefined;
  }

  private normalizeText(text: string): string {
    return text
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  private tokenize(text: string): string[] {
    const stopwords = new Set([
      "a", "an", "and", "the", "to", "of", "for", "in", "on", "at",
      "by", "with", "after", "before", "from", "our", "your", "their",
      "is", "was", "were", "be", "this", "that",
    ]);
    return this.normalizeText(text)
      .split(" ")
      .filter((token) => token.length > 1 && !stopwords.has(token));
  }

  private similarityScore(left: string, right: string): number {
    const leftTokens = new Set(this.tokenize(left));
    const rightTokens = new Set(this.tokenize(right));
    if (leftTokens.size === 0 || rightTokens.size === 0) {
      return 0;
    }
    const matches = [...leftTokens].filter((token) => rightTokens.has(token)).length;
    return matches / Math.max(leftTokens.size, rightTokens.size);
  }
}
