/**
 * Scenario template library — pre-built patterns without LLM generation (AC-443).
 *
 * Ports Python's autocontext/scenarios/templates/ to TypeScript.
 * Built-in templates are embedded in JS so the published npm package does not
 * depend on source-side JSON assets being present on disk.
 */

import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface RubricDimension {
  name: string;
  description: string;
  weight: number;
}

export interface TemplateSpec {
  name: string;
  description: string;
  taskPrompt: string;
  judgeRubric: string;
  outputFormat: string;
  maxRounds: number;
  qualityThreshold: number;
  judgeModel?: string;
  revisionPrompt?: string;
  sampleInput?: string;
  referenceContext?: string;
  requiredConcepts?: string[];
  rubricDimensions?: RubricDimension[];
}

// ---------------------------------------------------------------------------
// Built-in templates (embedded in the module for npm artifact safety)
// ---------------------------------------------------------------------------

const BUILTIN_TEMPLATES: readonly TemplateSpec[] = [
  {
    name: "content-generation",
    description:
      "Optimize article and blog content generation for quality and engagement. The agent produces written content evaluated on readability, engagement, factual accuracy, structure, and keyword integration.",
    taskPrompt:
      "Write a technical blog post about the benefits and trade-offs of microservices architecture for a software engineering audience.\n\nRequirements:\n- Length: 800-1200 words\n- Include at least 3 concrete examples or case studies\n- Address both benefits and challenges\n- Include actionable recommendations\n- Target keywords: microservices, scalability, deployment, monitoring\n\nProduce a well-structured, engaging article that balances technical depth with readability.",
    judgeRubric:
      "Evaluate the generated content on these dimensions:\n1. Readability (0.0-1.0): Is the content well-written, clear, and accessible to the target audience? Good flow and transitions?\n2. Engagement (0.0-1.0): Does the content capture and maintain reader interest? Are there compelling hooks, examples, and narrative elements?\n3. Factual accuracy (0.0-1.0): Are technical claims correct and well-supported? No hallucinated facts or statistics?\n4. Structure (0.0-1.0): Is the content well-organized with clear sections, logical progression, introduction, body, and conclusion?\n5. Keyword integration (0.0-1.0): Are target keywords naturally integrated without keyword stuffing?\n\nOverall score is a weighted average: readability 0.25, engagement 0.2, factual_accuracy 0.25, structure 0.15, keyword_integration 0.15.",
    outputFormat: "free_text",
    maxRounds: 2,
    qualityThreshold: 0.85,
    revisionPrompt:
      "Review the judge feedback and improve your article. Focus on the lowest-scoring dimensions. Strengthen factual claims with specific examples, improve transitions between sections, and ensure keywords are naturally integrated.",
    rubricDimensions: [
      { name: "readability", description: "Is the content clear and accessible to the target audience?", weight: 0.25 },
      { name: "engagement", description: "Does the content capture and maintain reader interest?", weight: 0.2 },
      { name: "factual_accuracy", description: "Are technical claims correct and well-supported?", weight: 0.25 },
      { name: "structure", description: "Is the content well-organized with clear sections?", weight: 0.15 },
      { name: "keyword_integration", description: "Are target keywords naturally integrated?", weight: 0.15 },
    ],
  },
  {
    name: "prompt-optimization",
    description:
      "Optimize a system prompt for a given task. The agent iteratively refines a system prompt to maximize output quality as measured by clarity, specificity, constraint coverage, output format compliance, and edge-case handling.",
    taskPrompt:
      "You are optimizing a system prompt for a given task.\n\nTask: Summarize technical documents into executive-friendly bullet points.\n\nInitial system prompt: \"Summarize the following document.\"\n\nProduce an improved system prompt that is clear, specific, includes output format constraints, handles edge cases, and maximizes the quality of the generated summaries.",
    judgeRubric:
      "Evaluate the optimized system prompt on these dimensions:\n1. Clarity (0.0-1.0): Is the prompt unambiguous and easy to follow?\n2. Specificity (0.0-1.0): Does the prompt provide concrete instructions rather than vague directives?\n3. Constraint coverage (0.0-1.0): Does the prompt specify output format, length limits, tone, and audience?\n4. Output format compliance (0.0-1.0): Does the prompt define a clear output structure?\n5. Edge-case handling (0.0-1.0): Does the prompt address what to do with ambiguous, missing, or conflicting information?\n\nOverall score is a weighted average: clarity 0.2, specificity 0.25, constraint_coverage 0.25, format_compliance 0.15, edge_case_handling 0.15.",
    outputFormat: "free_text",
    maxRounds: 3,
    qualityThreshold: 0.85,
    revisionPrompt:
      "Review the judge feedback and improve your system prompt. Focus on the lowest-scoring dimensions. Make the prompt more specific and add explicit handling for edge cases.",
    rubricDimensions: [
      { name: "clarity", description: "Is the prompt unambiguous and easy to follow?", weight: 0.2 },
      { name: "specificity", description: "Does the prompt provide concrete instructions?", weight: 0.25 },
      { name: "constraint_coverage", description: "Does the prompt specify output format, length, tone, audience?", weight: 0.25 },
      { name: "format_compliance", description: "Does the prompt define a clear output structure?", weight: 0.15 },
      { name: "edge_case_handling", description: "Does the prompt address ambiguous or missing information?", weight: 0.15 },
    ],
  },
  {
    name: "rag-accuracy",
    description:
      "Optimize RAG pipeline configuration for retrieval relevance. The agent tunes parameters like chunk size, overlap, top-k, and embedding strategy to maximize retrieval accuracy and answer grounding.",
    taskPrompt:
      "You are optimizing a Retrieval-Augmented Generation (RAG) pipeline.\nGiven the following configuration parameters and a set of test queries, produce an optimized configuration that maximizes retrieval relevance and answer quality.\n\nCurrent configuration:\n- chunk_size: 512 tokens\n- chunk_overlap: 50 tokens\n- top_k: 5\n- embedding_model: \"text-embedding-3-small\"\n- reranking: disabled\n- hybrid_search: disabled\n\nTest domain: Technical documentation for a cloud platform.\n\nProduce an optimized configuration with explanations for each parameter choice. Include the rationale for trade-offs between recall and precision.",
    judgeRubric:
      "Evaluate the RAG configuration optimization on these dimensions:\n1. Retrieval relevance (0.0-1.0): Do the parameter choices maximize the likelihood of retrieving relevant chunks?\n2. Answer grounding (0.0-1.0): Does the configuration support well-grounded answers with proper context windows?\n3. Citation accuracy (0.0-1.0): Does the configuration facilitate accurate source attribution?\n4. Hallucination detection (0.0-1.0): Does the configuration include mechanisms to reduce and detect hallucinations?\n5. Parameter justification (0.0-1.0): Are parameter choices well-justified with clear trade-off analysis?\n\nOverall score is a weighted average: retrieval_relevance 0.3, answer_grounding 0.25, citation_accuracy 0.2, hallucination_detection 0.15, parameter_justification 0.1.",
    outputFormat: "json_schema",
    maxRounds: 2,
    qualityThreshold: 0.8,
    revisionPrompt:
      "Review the judge feedback on your RAG configuration. Pay special attention to retrieval relevance and hallucination detection scores. Adjust parameters and add missing mechanisms as suggested.",
    rubricDimensions: [
      { name: "retrieval_relevance", description: "Do parameter choices maximize retrieval of relevant chunks?", weight: 0.3 },
      { name: "answer_grounding", description: "Does configuration support well-grounded answers?", weight: 0.25 },
      { name: "citation_accuracy", description: "Does configuration facilitate source attribution?", weight: 0.2 },
      { name: "hallucination_detection", description: "Are there mechanisms to reduce and detect hallucinations?", weight: 0.15 },
      { name: "parameter_justification", description: "Are parameter choices well-justified?", weight: 0.1 },
    ],
  },
] as const;

function cloneTemplateSpec(spec: TemplateSpec): TemplateSpec {
  return {
    ...spec,
    requiredConcepts: spec.requiredConcepts ? [...spec.requiredConcepts] : undefined,
    rubricDimensions: spec.rubricDimensions
      ? spec.rubricDimensions.map((dimension) => ({ ...dimension }))
      : undefined,
  };
}

function loadBuiltinTemplates(): Map<string, TemplateSpec> {
  return new Map(BUILTIN_TEMPLATES.map((spec) => [spec.name, cloneTemplateSpec(spec)]));
}

// ---------------------------------------------------------------------------
// TemplateLoader
// ---------------------------------------------------------------------------

export class TemplateLoader {
  private templates: Map<string, TemplateSpec>;

  constructor(templateDir?: string) {
    if (templateDir) {
      this.templates = new Map<string, TemplateSpec>();
      try {
        const files = readdirSync(templateDir).filter((f) => f.endsWith(".json")).sort();
        for (const file of files) {
          try {
            const raw = readFileSync(join(templateDir, file), "utf-8");
            const spec = JSON.parse(raw) as TemplateSpec;
            if (spec.name) this.templates.set(spec.name, spec);
          } catch { /* skip */ }
        }
      } catch { /* empty */ }
    } else {
      this.templates = loadBuiltinTemplates();
    }
  }

  /**
   * List all available templates.
   */
  listTemplates(): TemplateSpec[] {
    return [...this.templates.values()];
  }

  /**
   * Get a specific template by name.
   * @throws Error if template not found
   */
  getTemplate(name: string): TemplateSpec {
    const spec = this.templates.get(name);
    if (!spec) {
      const available = [...this.templates.keys()].join(", ");
      throw new Error(`Template '${name}' not found. Available: ${available}`);
    }
    return spec;
  }

  /**
   * Scaffold a template into a target directory.
   *
   * Creates:
   * - spec.json — the full template spec
   * - agent_task_spec.json — snake_case spec for the custom-loader
   * - scenario_type.txt — "agent_task" marker
   *
   * @param templateName - Name of the template to scaffold
   * @param targetDir - Directory to write files into
   * @param overrides - Optional fields to override in the spec
   */
  scaffold(
    templateName: string,
    targetDir: string,
    overrides?: Record<string, unknown>,
  ): void {
    const spec = this.getTemplate(templateName);
    const merged = overrides ? { ...spec, ...overrides } : spec;

    if (!existsSync(targetDir)) {
      mkdirSync(targetDir, { recursive: true });
    }

    // Write spec.json (camelCase, full spec)
    writeFileSync(
      join(targetDir, "spec.json"),
      JSON.stringify(merged, null, 2),
      "utf-8",
    );

    // Write agent_task_spec.json (snake_case, for custom-loader compatibility)
    writeFileSync(
      join(targetDir, "agent_task_spec.json"),
      JSON.stringify(
        {
          name: merged.name,
          task_prompt: merged.taskPrompt,
          judge_rubric: merged.judgeRubric,
          output_format: merged.outputFormat,
          judge_model: merged.judgeModel ?? "",
          max_rounds: merged.maxRounds,
          quality_threshold: merged.qualityThreshold,
          revision_prompt: merged.revisionPrompt ?? null,
          sample_input: merged.sampleInput ?? null,
          reference_context: merged.referenceContext ?? null,
          required_concepts: merged.requiredConcepts ?? null,
        },
        null,
        2,
      ),
      "utf-8",
    );

    // Write scenario_type.txt
    writeFileSync(join(targetDir, "scenario_type.txt"), "agent_task", "utf-8");
  }
}
