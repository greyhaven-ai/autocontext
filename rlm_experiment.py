"""RLM Experiment: Before/After validation of reference context + calibration features.

Runs the LLMJudge on the 3 baseline RLM posts:
1. WITHOUT reference context (simulates old behavior)
2. WITH reference context + required concepts (new behavior)
3. WITH reference context + calibration examples (full new behavior)

Shows whether the judge catches the fundamental factual error:
posts treat "RLM" as generic reasoning models, when RLM actually means
Recursive Language Models (context folding via sub-LLM delegation).
"""

import json
import os
import sys

sys.path.insert(0, "mts/src")

from mts.execution.judge import LLMJudge

# --- Config ---
import anthropic

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def llm_fn(system: str, user: str) -> str:
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text


# --- Posts ---
POSTS = {
    "Post 1: Agent Autonomy": """The reason most AI agents fail isn't intelligence — it's brittleness.

Give a standard LLM-based agent a multi-step task and watch what happens. It generates a plan, executes it linearly, and the moment something unexpected occurs, it either hallucinates a recovery or stalls. There's no real feedback loop. No course correction. Just token prediction on rails.

Reasoning Language Models change the mechanics here in a way that matters. By training with reinforcement learning on iterative reasoning chains, RLMs don't just produce answers — they evaluate, backtrack, and refine. That's not a philosophical distinction. It's an architectural one.

What this means practically: agents built on RLMs can attempt a subtask, assess whether the result actually satisfies the goal, and try a different approach if it doesn't. That loop — act, evaluate, revise — is the difference between an agent that can handle a 3-step workflow and one that can handle a 30-step workflow without a human checking every intermediate output.

We've been building agent systems at Grey Haven for clients in industries where the tasks aren't neat and predictable. Insurance workflows. Manufacturing diagnostics. The kind of work where edge cases are the norm, not the exception. In those environments, an agent that can reason about its own failures is worth ten that can't.

The real shift isn't "smarter AI." It's AI that degrades gracefully instead of catastrophically. That's what makes agents actually deployable.""",

    "Post 2: Token Efficiency": """Here's a cost problem nobody talks about enough: most production AI systems burn tokens on retries.

Your agent calls an LLM, gets a mediocre result, runs a validation check, fails, and calls the LLM again with a longer prompt that includes the failed attempt plus correction instructions. Repeat. Each retry doubles your token spend and latency. We've seen pipelines where 60% of total token cost comes from retry loops and prompt stuffing to compensate for first-pass failures.

RLMs offer a different path. Because the reasoning and self-correction happen inside the model's own inference process, you're paying for one (longer) generation instead of three or four round trips. The model thinks harder on one pass rather than thinking cheaply multiple times.

The math is counterintuitive. RLM inference costs more per call — sometimes 3-5x the tokens of a standard completion. But if that single call replaces a chain of prompt → validate → re-prompt → validate → re-prompt, total cost drops. In our testing, tasks that previously required an average of 2.8 LLM calls to reach acceptable quality needed 1.1 calls with a reasoning model. Net token spend fell roughly 40%.

This matters most for high-volume production workloads where you're already paying for orchestration layers, validation logic, and retry infrastructure. RLMs don't just save tokens — they simplify your architecture. Less code to maintain, fewer failure modes, faster end-to-end latency.

The cheapest token is the one you never have to send.""",

    "Post 3: Contrarian Take": """Most of the conversation around Reasoning Language Models assumes the bottleneck in AI is reasoning quality. I think that's wrong.

The bottleneck is knowing *when* to reason.

RLMs are genuinely better at complex multi-step problems. No argument there. But the vast majority of real-world AI tasks don't need extended reasoning. They need fast pattern matching, reliable formatting, and consistent execution. Deploying a reasoning model to extract invoice line items is like hiring a PhD mathematician to do your bookkeeping. It'll work, but you're paying for capability you don't need, and it'll be slower.

The harder problem — the one nobody's building good tooling for yet — is routing. Deciding at inference time whether a given input needs 10 tokens of thought or 10,000. The models themselves aren't good at this. They'll happily burn through a long chain-of-thought on a question that deserved a one-line answer.

What I'd actually watch for isn't better reasoning models. It's better orchestration that knows when to invoke them. A system that routes simple extractions to a fast, cheap model and escalates genuine ambiguity to an RLM — that's where the production value is.

The teams that will get the most out of RLMs are the ones that use them sparingly. Not as a default, but as an escalation path. The rest will end up with slower, more expensive systems and a vague sense that reasoning models were overhyped.

They weren't. They were just over-applied.""",
}

TASK_PROMPT = (
    "Write a LinkedIn post about Recursive Language Models (RLMs) for a technical "
    "but accessible audience. The post should demonstrate understanding of what RLMs "
    "actually are — specifically the context folding architecture using persistent "
    "Python REPLs and sub-LLM delegation, as introduced by Alex Zhang (Oct 2025) "
    "and researched by Prime Intellect."
)

RUBRIC = (
    "Evaluate on: (1) Factual accuracy — does the post correctly describe what RLMs are? "
    "(2) Voice — direct, opinionated, no hype language. "
    "(3) Technical depth — accessible but substantive. "
    "(4) Engagement — would this perform well on LinkedIn?"
)

REFERENCE_CONTEXT = """RLM (Recursive Language Model) is a specific architecture for context folding — recursively compressing and reshaping an agent's own context to prevent context rot and keep ultra-long, multi-step rollouts cheap and reliable.

Key facts:
- RLM is NOT "Reasoning Language Models" or generic chain-of-thought reasoning
- RLM allows an LLM to use a persistent Python REPL to inspect and transform its input data
- The model calls sub-LLMs (fresh instances of itself) from within the REPL
- Sub-LLM calls can be parallelized via llm_batch
- The RLM answers via a Python variable (answer["content"] + answer["ready"])
- Introduced by Alex Zhang, October 2025
- Paper: https://arxiv.org/abs/2512.24601
- Prime Intellect is a major research backer
- The key innovation is the model managing its own context window as a first-class capability
- It's about context management and recursive delegation, not just "reasoning harder"
"""

REQUIRED_CONCEPTS = [
    "Context folding (not just chain-of-thought reasoning)",
    "Persistent Python REPL for context manipulation",
    "Sub-LLM delegation",
    "Alex Zhang / Prime Intellect origin",
    "RLM = Recursive Language Model (not Reasoning Language Model)",
]

CALIBRATION_EXAMPLES = [
    {
        "human_score": 0.15,
        "human_notes": (
            "Fundamentally misunderstands the topic. Treats 'RLM' as 'Reasoning Language Models' "
            "(generic o1/o3-style chain-of-thought) when it actually means 'Recursive Language Models' — "
            "a specific context folding architecture using Python REPLs and sub-LLM delegation. "
            "Every claim in the post is about reasoning models in general, not RLMs specifically. "
            "Good voice and structure, but the content is about the wrong thing entirely."
        ),
        "agent_output": "Reasoning Language Models change the mechanics here...",
    },
    {
        "human_score": 0.85,
        "human_notes": (
            "Accurately describes RLM as a context folding architecture. Mentions Python REPL, "
            "sub-LLM delegation, and Prime Intellect. Good voice — direct, no hype. "
            "Could be more specific about the paper and experimental results."
        ),
        "agent_output": "The real breakthrough in RLMs isn't reasoning — it's context management...",
    },
]


def run_experiment():
    print("=" * 80)
    print("RLM EXPERIMENT: Before/After Validation")
    print("=" * 80)
    print()

    results = {}

    for post_name, post_text in POSTS.items():
        print(f"\n{'=' * 60}")
        print(f"  {post_name}")
        print(f"{'=' * 60}")

        # --- Run 1: No reference context (old behavior) ---
        print("\n  [1/3] Judge WITHOUT reference context...")
        judge_no_ctx = LLMJudge(
            model="claude-sonnet-4-20250514",
            rubric=RUBRIC,
            llm_fn=llm_fn,
        )
        result_no_ctx = judge_no_ctx.evaluate(TASK_PROMPT, post_text)
        print(f"        Score: {result_no_ctx.score:.2f}")
        print(f"        Dimensions: {result_no_ctx.dimension_scores}")
        print(f"        Reasoning: {result_no_ctx.reasoning[:200]}...")

        # --- Run 2: With reference context + required concepts ---
        print("\n  [2/3] Judge WITH reference context...")
        judge_with_ctx = LLMJudge(
            model="claude-sonnet-4-20250514",
            rubric=RUBRIC,
            llm_fn=llm_fn,
        )
        result_with_ctx = judge_with_ctx.evaluate(
            TASK_PROMPT,
            post_text,
            reference_context=REFERENCE_CONTEXT,
            required_concepts=REQUIRED_CONCEPTS,
        )
        print(f"        Score: {result_with_ctx.score:.2f}")
        print(f"        Dimensions: {result_with_ctx.dimension_scores}")
        print(f"        Reasoning: {result_with_ctx.reasoning[:200]}...")

        # --- Run 3: With reference context + calibration examples ---
        print("\n  [3/3] Judge WITH reference context + calibration...")
        judge_full = LLMJudge(
            model="claude-sonnet-4-20250514",
            rubric=RUBRIC,
            llm_fn=llm_fn,
        )
        result_full = judge_full.evaluate(
            TASK_PROMPT,
            post_text,
            reference_context=REFERENCE_CONTEXT,
            required_concepts=REQUIRED_CONCEPTS,
            calibration_examples=CALIBRATION_EXAMPLES,
        )
        print(f"        Score: {result_full.score:.2f}")
        print(f"        Dimensions: {result_full.dimension_scores}")
        print(f"        Reasoning: {result_full.reasoning[:200]}...")

        results[post_name] = {
            "no_context": {
                "score": result_no_ctx.score,
                "dimensions": result_no_ctx.dimension_scores,
                "reasoning": result_no_ctx.reasoning,
            },
            "with_context": {
                "score": result_with_ctx.score,
                "dimensions": result_with_ctx.dimension_scores,
                "reasoning": result_with_ctx.reasoning,
            },
            "full": {
                "score": result_full.score,
                "dimensions": result_full.dimension_scores,
                "reasoning": result_full.reasoning,
            },
        }

    # --- Summary ---
    print("\n\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"\n{'Post':<30} {'No Context':>12} {'+ Ref Context':>14} {'+ Calibration':>14} {'Delta':>8}")
    print("-" * 80)
    for post_name, r in results.items():
        no_ctx = r["no_context"]["score"]
        with_ctx = r["with_context"]["score"]
        full = r["full"]["score"]
        delta = no_ctx - full
        print(f"{post_name:<30} {no_ctx:>12.2f} {with_ctx:>14.2f} {full:>14.2f} {delta:>+8.2f}")

    # Save results
    with open("/workspace/content/research/rlm-experiment-results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nFull results saved to /workspace/content/research/rlm-experiment-results.json")

    return results


if __name__ == "__main__":
    run_experiment()
