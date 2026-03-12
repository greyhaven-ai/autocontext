# Prompt Optimization Template

Optimize a system prompt for a given task. The agent iteratively refines a system prompt to maximize output quality.

## Overview

This template sets up an agent task where the goal is to produce an optimized system prompt. The LLM judge evaluates the prompt across five dimensions:

- **Clarity** (weight: 0.20) -- Is the prompt unambiguous?
- **Specificity** (weight: 0.25) -- Are instructions concrete?
- **Constraint Coverage** (weight: 0.25) -- Does it specify format, length, tone?
- **Output Format Compliance** (weight: 0.15) -- Is there a defined output structure?
- **Edge-Case Handling** (weight: 0.15) -- Does it address ambiguous inputs?

## Quick Start

```bash
# Scaffold a new scenario from this template
mts new-scenario --template prompt-optimization --name my-prompt-task

# The scaffolded task is written under knowledge/_custom_scenarios/my-prompt-task
# and becomes available to Autocontext's agent-task tooling after load/restart.
```

## Customization

Edit `spec.yaml` to change:

- `task_prompt` -- The task description and initial prompt to optimize
- `judge_rubric` -- Evaluation criteria and dimension weights
- `max_rounds` -- Number of improvement iterations (default: 3)
- `quality_threshold` -- Score target to stop early (default: 0.85)
- `revision_prompt` -- Instructions for how to improve after feedback

## Files

- `spec.yaml` -- Template configuration
- `example_input.json` -- Sample input state
- `example_output.json` -- Expected output format
