# RAG Accuracy Template

Optimize RAG pipeline configuration for retrieval relevance and answer quality.

## Overview

This template sets up an agent task where the goal is to produce an optimized RAG pipeline configuration. The LLM judge evaluates across five dimensions:

- **Retrieval Relevance** (weight: 0.30) -- Do parameters maximize relevant chunk retrieval?
- **Answer Grounding** (weight: 0.25) -- Does config support well-grounded answers?
- **Citation Accuracy** (weight: 0.20) -- Does config facilitate source attribution?
- **Hallucination Detection** (weight: 0.15) -- Are there anti-hallucination mechanisms?
- **Parameter Justification** (weight: 0.10) -- Are choices well-justified?

## Quick Start

```bash
# Scaffold a new scenario from this template
mts new-scenario --template rag-accuracy --name my-rag-task

# The scaffolded task is written under knowledge/_custom_scenarios/my-rag-task
# and becomes available to Autocontext's agent-task tooling after load/restart.
```

## Customization

Edit `spec.yaml` to change:

- `task_prompt` -- The RAG domain and current configuration to optimize
- `judge_rubric` -- Evaluation criteria and dimension weights
- `output_format` -- Set to `json_schema` for structured config output
- `max_rounds` -- Number of improvement iterations (default: 2)
- `quality_threshold` -- Score target to stop early (default: 0.8)

## Files

- `spec.yaml` -- Template configuration
- `example_input.json` -- Sample RAG configuration input
- `example_output.json` -- Expected optimized configuration format
