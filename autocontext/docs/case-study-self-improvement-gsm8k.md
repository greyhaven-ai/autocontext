# Case study: verifier-driven self-improvement on GSM8K (an honest negative)

This is a negative result, reported in full because the path to it was more valuable than a
tidy win would have been. The question: can a model improve its own GSM8K accuracy using only
autocontext's scenario verifier (no teacher, no labelled solutions), via STaR (expert iteration)
or GRPO (RLVR)? Across every configuration tried, the answer was no. But getting to a _trustworthy_
no surfaced three real bugs that had been silently breaking RLVR, and the way it failed says
something precise about when verifier-driven self-improvement does and does not pay off.

## Result

Held-out GSM8K test accuracy (disjoint split), no teacher, verifier-only reward:

| Method                                         | Base | Final | Delta  |
| ---------------------------------------------- | ---- | ----- | ------ |
| STaR, Qwen2.5-0.5B (local MLX)                 | 0.25 | 0.125 | -0.125 |
| STaR, Qwen2.5-1.5B (any-correct + rationalize) | 0.63 | 0.59  | -0.04  |
| **GRPO/RLVR, Qwen2.5-1.5B (1000 steps)**       | 0.63 | 0.58  | -0.05  |

Both methods regress a capable base. The interesting part is _why_, and that it took real
engineering to make the GRPO number trustworthy at all.

## The engineering story: three bugs hiding behind "GRPO is flat"

GRPO looked flat in early runs. Watching the live training metrics on Modal (not just the final
number) showed it was not flat, it was broken, in three stacked ways. Each was found by reading
`completions/clipped_ratio`, `mean_terminated_length`, and `rewards/_reward/mean` mid-run:

1. **Truncation.** `max_completion_length` defaulted to 256. GSM8K reasoning plus a final
   `Answer: N` exceeds that, so every completion was clipped before the answer, the verifier
   scored them all 0, reward variance was 0, and GRPO got no gradient. Fixed by raising the
   default to 512 and threading it through the CLI (PR #1067).
2. **No chat template.** Even at 512, `clipped_ratio` stayed at 1.0 and `mean_terminated_length`
   at 0: completions still never stopped. The GRPO prompts were raw strings, so TRL skipped the
   instruct model's chat template, the model never entered assistant mode, and it never emitted
   EOS. Fixed by making prompts conversational (PR #1070).
3. **JSON-only reward.** The reward adapter ran `extract_json_object` on every completion, so a
   correct free-text GSM8K answer scored 0 because it is not a JSON construction. Fixed with a
   hybrid path: JSON when present (game / construction scenarios), raw text otherwise (PR #1070).

After all three, GRPO was genuinely functional for the first time: `clipped_ratio` 1.0 -> 0.03,
`mean_terminated_length` 0 -> ~277 tokens, `reward` 0 -> ~0.65 with real variance, loss and
gradient nonzero. Only then was the held-out number (0.58) worth reporting.

## Why it did not improve

The decisive observation from the functional GRPO run: **train reward climbed (0.65 -> 0.74)
while held-out accuracy fell (0.63 -> 0.58).** The optimizer was improving the policy on the
reward; it just was not generalizing. That is overfitting, not an inability to learn. The
configuration used `beta = 0` (no KL penalty), so nothing anchored the policy to the base
distribution over ~2.6 epochs on 384 prompts, and it drifted.

STaR failed for a related but distinct reason. The self-consistency filter kept problems the
1.5B already solves, so SFT mostly imitated known-good behavior and added no capability; the
rationalization step (learning from problems it cannot yet solve, by working backward from the
gold answer) barely fired at this scale. At 0.5B it failed differently: only ~2/32 problems were
reliably solved, so most verifier-passing chains were lucky-wrong reasoning that hit the right
integer, and training on them taught bad reasoning.

## Does it scale, or is it just no?

It is not a fundamental no. Verifier-driven RL on math is one of the better-established scaling
results in recent post-training (pure GRPO RLVR on a base model drove large math gains in work
like DeepSeek-R1-Zero; STaR and ReST-EM improve math at their intended scale). Our negative is
explained by fixable, non-fundamental causes:

- **Config:** `beta = 0` removed the regularization that keeps RLVR from overfitting. The single
  highest-leverage fix is a KL penalty (`beta > 0`) and/or early-stopping on held-out.
- **Benchmark:** GSM8K is near-saturated for capable models (1.5B ~0.63, 3B ~0.80) and partly in
  pretraining. The headroom is thin and the binary integer verifier is noisy. Scaling gains show
  up on harder benchmarks (MATH, AIME) where the base genuinely cannot yet solve the problems and
  the "learn from what you cannot do" mechanism has room to work.
- **Scale:** 1.5B is small relative to where the dramatic RLVR results live.

So more compute on GSM8K-at-1.5B-with-`beta=0` would stay flat-to-negative. The experiment that
would actually settle the scaling claim is GRPO with a KL penalty on a benchmark with real
headroom, not more steps on a saturated one.

## The methodology lesson

The result that mattered was not "self-improvement does not work on GSM8K." It was that **the
final number is worthless until the training metrics are sane.** Two full GRPO runs (and a lot
of GPU) produced "flat" results that were actually three different silent failures. Reading
`clipped_ratio` / `mean_terminated_length` / `reward_std` live caught what the held-out number
hid. The fixes those runs forced out (PRs #1065, #1067, #1070) made autocontext's training
backends genuinely work, which outlasts any single benchmark answer.

## Reproduce

The runs used autocontext's `trl` GRPO backend and `mlxlm` SFT backend with a GSM8K
`AgentTaskInterface` scenario (exact-integer verifier, disjoint train/test splits) on Modal
A100s. The harness runs a baseline probe plus STaR and GRPO arms with held-out greedy evaluation,
detached so a long run survives client disconnects. A `--grpo-only` mode runs the RLVR arm alone.
The corrected next experiment (GRPO with `beta > 0`, then a harder benchmark) is a one-flag change
to the GRPO config.
