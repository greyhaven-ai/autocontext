# Content Generation Template

Optimize article and blog content generation for quality and engagement signals.

## Overview

This template sets up an agent task where the goal is to produce high-quality written content. The LLM judge evaluates across five dimensions:

- **Readability** (weight: 0.25) -- Is the content clear and accessible?
- **Engagement** (weight: 0.20) -- Does it capture reader interest?
- **Factual Accuracy** (weight: 0.25) -- Are claims correct and supported?
- **Structure** (weight: 0.15) -- Is it well-organized?
- **Keyword Integration** (weight: 0.15) -- Are keywords naturally used?

## Quick Start

```bash
# Scaffold a new scenario from this template
mts new-scenario --template content-generation --name my-blog-task

# The scaffolded task is written under knowledge/_custom_scenarios/my-blog-task
# and becomes available to Autocontext's agent-task tooling after load/restart.
```

## Customization

Edit `spec.yaml` to change:

- `task_prompt` -- The content topic, requirements, and target keywords
- `judge_rubric` -- Evaluation criteria and dimension weights
- `max_rounds` -- Number of improvement iterations (default: 2)
- `quality_threshold` -- Score target to stop early (default: 0.85)
- `revision_prompt` -- Instructions for content improvement

## Files

- `spec.yaml` -- Template configuration
- `example_input.json` -- Sample input parameters
- `example_output.json` -- Expected output format
