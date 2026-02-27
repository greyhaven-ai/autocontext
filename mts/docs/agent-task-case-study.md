# Case Study: How Reference Context Catches Domain-Specific Errors

MTS's agent task evaluation uses an LLM-as-judge to score agent outputs. This case study documents a real experiment that exposed a critical gap in judge-only evaluation — and how reference context, human feedback calibration, and context preparation fix it.

## The Setup

We used MTS to evaluate LinkedIn posts about **RLMs (Recursive Language Models)** — a specific architecture for context folding via persistent Python REPLs and sub-LLM delegation, introduced by Alex Zhang (Oct 2025) and researched by Prime Intellect.

The agent produced 3 well-written posts. Good voice, good structure, technically substantive. One problem: every post treated "RLM" as "Reasoning Language Models" — generic o1/o3-style chain-of-thought reasoning. The posts were confidently, completely wrong about the core topic.

## The Experiment

We ran each post through `LLMJudge.evaluate()` under three conditions:

1. **No reference context** — vague task prompt ("Write about RLMs"), rubric checks voice/depth/accuracy/engagement
2. **With reference context** — same prompt, but `reference_context` and `required_concepts` provided
3. **With calibration** — reference context plus `calibration_examples` from human feedback

### Results

```
Post                         No Context   + Ref Context   + Calibration
----------------------------------------------------------------------
Post 1: Agent Autonomy            0.85           0.20           0.15
Post 2: Token Efficiency          0.85           0.20           0.25
Post 3: Contrarian Take           0.85           0.10           0.10
----------------------------------------------------------------------
AVERAGE                           0.85           0.17           0.17
```

### Factual Accuracy Dimension

```
Post                         No Context   + Ref Context   + Calibration
----------------------------------------------------------------------
Post 1: Agent Autonomy            0.80           0.00           0.00
Post 2: Token Efficiency          0.90           0.10           0.10
Post 3: Contrarian Take           0.90           0.00           0.00
----------------------------------------------------------------------
AVERAGE                           0.87           0.03           0.03
```

## What Happened

**Without reference context**, the judge scored 0.85 average. It called the posts "strong" with "solid understanding." The judge had no domain knowledge to contradict the agent's confident misuse of the term — the posts *sounded* right.

**With reference context**, scores dropped to 0.17. The judge immediately identified the fundamental error: the posts describe generic reasoning models, not Recursive Language Models. Factual accuracy went from 0.87 to 0.03.

**With calibration examples**, scores were nearly identical to reference-only (0.17). This makes sense for a binary error (wrong definition). Calibration adds more value for subtler quality distinctions where the "right" answer isn't black-and-white.

## Why This Matters

LLM judges are good at evaluating style, structure, and general coherence. They are **bad at catching domain-specific factual errors** unless given ground truth to evaluate against.

This isn't a niche problem. Any task where:
- The agent writes about a specific technology, company, or concept
- Domain knowledge is required to distinguish correct from plausible-sounding-but-wrong
- The judge LLM might not have up-to-date information

...will hit this failure mode without reference context.

## Features Used

### Reference Context (`AgentTaskSpec.reference_context`)

Authoritative domain knowledge injected into the judge prompt. When present, `LLMJudge` adds a mandatory `factual_accuracy` dimension and instructs the judge to score against the reference.

```python
spec = AgentTaskSpec(
    task_prompt="Write about RLMs...",
    judge_rubric="Evaluate accuracy, voice, depth, engagement",
    reference_context="RLM = Recursive Language Model. A context folding architecture...",
    required_concepts=[
        "Context folding (not chain-of-thought)",
        "Persistent Python REPL",
        "Sub-LLM delegation",
    ],
)
```

### Calibration Examples (`LLMJudge.evaluate(calibration_examples=...)`)

Human-scored examples injected into the judge prompt. Teaches the judge what score levels mean in practice.

```python
result = judge.evaluate(
    task_prompt, agent_output,
    reference_context=spec.reference_context,
    required_concepts=spec.required_concepts,
    calibration_examples=[
        {
            "human_score": 0.15,
            "human_notes": "Wrong definition of RLM. Treats it as reasoning models.",
            "agent_output": "Reasoning Language Models change the mechanics...",
        },
        {
            "human_score": 0.85,
            "human_notes": "Accurate. Describes context folding and sub-LLM delegation.",
            "agent_output": "The breakthrough in RLMs isn't reasoning — it's context management...",
        },
    ],
)
```

### Context Preparation (`AgentTaskInterface.prepare_context()`)

Tasks can define a preparation stage that runs before generation. This is where research, document loading, and context validation happen — ensuring the agent has accurate information before it writes.

```python
class MyTask(AgentTaskInterface):
    def prepare_context(self, state: dict) -> dict:
        state["reference_context"] = load_reference_docs()
        state["sources"] = fetch_source_urls()
        return state

    def validate_context(self, state: dict) -> list[str]:
        errors = []
        if "reference_context" not in state:
            errors.append("missing reference context")
        return errors
```

## Key Takeaway

If your agent task requires domain knowledge, **always provide reference context**. The judge cannot evaluate what it doesn't know. A 0.68-point scoring swing on the same content — from "great" to "fundamentally wrong" — demonstrates that style-only evaluation is insufficient for factual tasks.

## Reproducing This Experiment

The experiment scripts are in the repo root:

- `rlm_experiment.py` — detailed task prompt (judge has hints)
- `rlm_experiment_v2.py` — vague task prompt (fair baseline, starker results)

Run with:
```bash
ANTHROPIC_API_KEY=sk-... mts/.venv/bin/python rlm_experiment_v2.py
```

Results are saved to `content/research/rlm-experiment-v2-results.json`.
