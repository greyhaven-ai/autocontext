# MTS Plan Review — Notes for Discussion

Hey — went deep on the plan and did a bunch of research. Two things to go through: a potential addition (RLMs) that could help with context scaling, and a list of concerns we should sort out before we start building.

---

## RLMs (Recursive Language Models) — Worth Adding?

Paper: https://arxiv.org/html/2512.24601v2  
PrimeIntellect already implemented this in verifiers: https://www.primeintellect.ai/blog/rlm

### What it is
Instead of feeding a massive prompt into the LLM's context window (where quality degrades — "context rot"), you load the data as a **variable in a Python REPL**. The model never sees the raw data. It gets metadata ("you have 200K characters of replay data") and writes code to peek at slices, search for patterns, and call fresh sub-LLMs on the pieces that matter. The model programs its own attention.

Key results from the paper:
- RLM(GPT-5) scored **91.3%** on a 6-11M token QA task vs **0%** for base GPT-5 (input too large to even attempt)
- On information-dense tasks, RLM got **58% F1** where base GPT-5 scored **<0.1%**
- Costs were comparable or cheaper at the median
- Even a fine-tuned 8B model improved 28.3% with the scaffold

### Where it helps MTS specifically

**Analyst — replay analysis.** As games get more complex, replay narratives grow. Instead of cramming the full replay into context, treat it as an RLM environment variable. The Analyst writes code to scan specific game phases, extract patterns from specific turns, and call sub-LLMs to analyze each phase. The root Analyst synthesizes. This means we can handle much longer/richer games without quality degradation.

**Architect — system-wide view.** The Architect needs the broadest context (playbooks, analysis reports, tool libraries, metrics across all generations). RLM lets it programmatically scan history and only pull relevant context into focused sub-calls. Less wasted Opus credits on irrelevant tool-building.

**Coach — playbook growth.** By Gen 20+, the playbook could be large and contradictory. RLM lets the Coach search for relevant sections and evaluate whether specific advice still holds, instead of "read everything and figure it out."

**Cross-generation queries.** Any agent could write code to query the full generation history — filtering by generation, sorting by score, comparing pairs. Exactly the kind of dense data access RLMs are built for.

### What it takes to build
We're not training a model — we're building the scaffold. We already have sandboxed Python execution via PrimeIntellect Sandboxes. The core is:
1. Wrap agent inputs as REPL variables
2. Expose peek/search/sub_llm functions in the REPL
3. Prompt Claude to operate recursively instead of consuming everything in one pass
4. Use Haiku for sub-calls to keep costs down

### My recommendation
Don't RLM everything. Apply it to the **Analyst** and **Architect** only — they have the biggest context appetite. Keep Competitor and Coach on direct context injection for early generations. Build the RLM scaffold as a reusable component that becomes another shareable artifact ("RLM scaffold for Claude agents").

This also strengthens the PrimeIntellect connection since they already have RLMEnv in verifiers, and gives us a differentiated angle vs other hackathon teams doing pure Ralph Wiggum loops.

---

## Concerns with the Current Plan

Roughly ordered by severity. Some of these might be things you've already thought through — flag anything that's already handled.

### Critical — Could Kill the Demo

**1. Subagents can't spawn subagents.**  
The Claude docs explicitly say this. If our agents are Agent SDK subagents, they can't spawn their own sub-workers. We need to decide: is each agent a top-level process (via `claude -p` or direct SDK `query()`) or a subagent? The plan mixes both ("CLI dispatch for outer loop, Agent SDK subagents for inner agents") and we need to figure out exactly how the layers connect.

**2. No cost estimate.**  
The plan says model tiering "stretches $500 significantly" but doesn't show the math. We need to estimate: tokens per strategy-writing call × matches per generation × generations. One report showed 49 subagents burning $8K-$15K in 2.5 hours. If each generation costs $25 in API calls, we get 20 generations. If it costs $50, we get 10. If something goes wrong and it costs $100, we get 5 and the demo is thin. We should run a cost estimate before writing any code, and build in hard per-generation cost caps with kill switches.

**3. Grid CTF doesn't exist yet.**  
20x20 grid, fog of war, 3 unit types, terrain, observations, actions, narrative generation — that's easily 2-3 days for one person. If it's not rock-solid by day 3, the entire demo chain is blocked. Othello has known rules and existing implementations everywhere. I think we should **start with Othello as primary, Grid CTF as second scenario**, not the other way around. The pluggability demo (same loop, different scenario) is actually stronger when the first scenario was trivial to implement.

**4. The tournament runner is load-bearing and underspecified.**  
It orchestrates sandboxed execution of untrusted code, manages timeouts, collects results, handles crashes, and produces structured replays. It's the most complex infrastructure piece and it's one sentence in the plan. We should design this first.

### Serious — Could Degrade the Demo

**5. LLM-as-judge for the Analyst is circular.**  
The Analyst's backpressure uses an LLM judge checking "specific, actionable, novel." But another LLM might pass plausible-sounding but useless analysis. We need at least one non-LLM signal — like "did the strategy that followed this analysis actually improve scores?"

**6. Coach revert mechanism is too blunt.**  
If scores dip for 2 generations after a playbook update, the Coach reverts. But the dip might be the Competitor misinterpreting the new playbook, not the playbook being wrong. Suggestion: reduce weight on new sections or flag them for review, rather than full nuclear revert.

**7. Architect scheduling (every 3rd gen) is rigid.**  
Too infrequent if a critical tool is needed early, too frequent if it burns Opus credits on tools nobody needs yet. Should be triggered when the Analyst identifies repeated capability gaps, not on a fixed schedule.

**8. `prepare-prompt` is doing critical invisible work.**  
This is the glue that makes scenario pluggability work — composing `describe_rules()` with agent base prompts at runtime. It's one line in the plan but needs real design: token budget, truncation logic, how scenario content is prioritized vs. playbook content when context gets tight.

**9. No observability until the last phase.**  
The dashboard is phase 7. During days 1-5, we need to see what's happening inside the loop — what strategies are being generated, what the Analyst is saying, whether the Coach is improving or thrashing. Suggestion: build a simple JSON log per generation from day 1. Even just dumping `{gen: N, elo: [...], playbook_diff: "...", top_failure: "..."}` to a file is enough.

### Moderate — Worth Addressing

**10. Elo is noisy at low sample sizes.**  
If we only run 5-10 matches per generation (to save budget), Elo ratings will be volatile. A 6/10 vs 4/10 win rate could be luck. We need enough matches to make the signal real, or supplement with average score margin which converges faster.

**11. Sandbox execution latency adds up.**  
Even with sub-second provisioning, each match is: spin up → upload strategy → execute game → collect results → tear down. At 5 seconds per match × 20 matches per generation × 20 generations = 33 minutes of just execution, not counting API calls. We should profile this early.

**12. "Shareable artifacts" claim is untested.**  
The plan says playbooks and tools are "immediately consumable by other Claude agents." But a Grid CTF playbook might be full of domain-specific jargon. We should actually test this: take a tool the Architect builds for one scenario and hand it to a fresh agent on another scenario. If it helps, great demo moment. If not, soften the claim.

**13. Scope vs. timeline.**  
Seven implementation phases, two people, one week. Phases 1-3 alone (core framework + first loop + full agent team) could consume the full week. The plan includes Fly.io, PrimeIntellect Sandboxes, Docker, JS+Canvas viz, WebSocket streaming — a lot of infrastructure. Suggestion: cut the viz to a static HTML page reading JSON logs. WebSocket streaming is a stretch goal.

**14. No artifact versioning.**  
Playbooks, tools, and analysis reports evolve over generations. If the Coach reverts the playbook, what about tools the Architect built based on the now-reverted version? We need generation-tagged snapshots of everything, not just the playbook.

**15. Drop Market Sim entirely.**  
It's listed as a stretch but it fundamentally changes the problem — continuous action spaces, stochastic environments, different strategy representations. It's not a clean scenario swap. Grid CTF → Othello is the right generalizability proof. Market Sim would muddy the demo.

---

## Suggested Priority Order

1. Cost estimate (before anything else)
2. Decide the agent spawning architecture (top-level processes vs subagents)
3. Build tournament runner + Othello scenario (get matches executing in sandboxes)
4. Single Competitor loop on Othello (prove one agent improves)
5. Full agent team + backpressure
6. Grid CTF as second scenario (pluggability proof)
7. Architect + tool building
8. RLM scaffold for Analyst/Architect (if time permits)
9. Viz / demo prep

Let me know what you think — especially on the Othello-first vs Grid CTF-first question and the subagent architecture.
