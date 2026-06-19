# OPD/GKD + GRPO mixture experiment protocol (AC-798)

Use `autocontext/scripts/plan_opd_grpo_mixture_experiment.py` to emit the matched-compute plan:

```bash
PYTHONPATH=autocontext/src python autocontext/scripts/plan_opd_grpo_mixture_experiment.py \
  --scenario gsm8k --seed 0 --seed 1 --seed 2 --step 1000 --step 2000 --prompts 384
```

The protocol compares four arms at the same prompt and step budget:

1. verifier-only GRPO
2. full OPD/GKD
3. positive-pressure OPD/GKD
4. mixed positive-pressure OPD + GRPO (`--training-mixture positive_opd=0.5,grpo=0.5`)

Record `final_score`, `heldout_score`, `response_length`, `diversity`, `entropy`, `kl`, `token_pressure`, and `cost_time` for every seed. Use the AC-787/AC-789 matched-compute methodology when possible.

The mixed arm is not a default recommendation. Promote it only when held-out score improves over the best baseline by the configured margin and collapse checks stay clear.
