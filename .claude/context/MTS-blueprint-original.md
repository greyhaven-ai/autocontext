MTS: Monitoring The Situation
Hackathon Application

What do you want to build for this hackathon?
MTS (Monitoring The Situation) — a self-improving multi-agent framework with a pluggable scenario engine. Claude agent teams (competitors, analysts, coaches, architects) run in iterative self-improvement loops inspired by the Ralph Wiggum technique, competing in scenarios, analyzing outcomes, evolving strategy playbooks, and building their own tools. The scenario is interchangeable — tactical simulations, market participation, operational wargames, cybersecurity exercises — the agents and the improvement loop stay the same. The evolved playbooks and tools are shareable artifacts any Claude agent can immediately use. We're demonstrating it through competitive gameplay, but the framework applies to any domain with measurable outcomes.


Concept
Four specialized Claude agents, each in its own iterative improvement loop, collaborate through shared artifacts to get measurably better at any pluggable scenario. The framework utilizes Ralph Wiggum-like loops — tight iterations, fresh context per cycle, strong backpressure — extended to multi-agent with shared state and self-evolving infrastructure.
Four layers of self-improvement:

Tactical — Competitor strategies get better each generation (measurable via Elo)
Knowledge — The playbook accumulates insights in natural language (readable, shareable)
Infrastructure — The Architect builds tools that make all other agents more capable (self-evolving)
Operational — Agents maintain skills/ files that teach future agent instances how to handle specific workflows. When a workflow fails and gets fixed, the fix is written back as a skill — implicit knowledge becomes explicit. Skills are symlinked into .claude/skills/ so every subsequent agent spawn inherits them automatically.

Architecture: Clean control plane / data plane separation. The outer bash loop + generation state is the control plane; sandboxed strategy execution is the data plane. This split lets two developers work the layers independently during the hackathon week.
Inspiration: Ralph Wiggum technique (iterative loops with backpressure), Loom (self-evolutionary infrastructure), Anthropic's parallel-agent C compiler (multi-agent coordination via shared state), Ramp Open-Inspect (supervisor pattern, snapshot warming, event streaming).

Scenario Interface
The core abstraction. Any domain that implements this plugs into the self-improvement loop. Key differentiator: a natural language layer on every method — describe_rules(), get_observation().narrative, replay_to_narrative() — because Claude reasons from text, not tensors.
pythonclass ScenarioInterface(ABC):
    # Rules & strategy contract (natural language, injected into agent prompts)
    def describe_rules(self) -> str: ...
    def describe_strategy_interface(self) -> str: ...
    def describe_evaluation_criteria(self) -> str: ...

    # Execution loop
    def initial_state(self, seed=None) -> Any: ...
    def get_observation(self, state, player_id) -> Observation: ...
    def validate_actions(self, state, player_id, actions) -> tuple[bool, str]: ...
    def step(self, state, actions) -> Any: ...
    def is_terminal(self, state) -> bool: ...
    def get_result(self, state) -> Result: ...

    # Analysis & viz
    def replay_to_narrative(self, replay) -> str: ...
    def render_frame(self, state) -> dict: ...

    # Optional hooks
    def seed_tools(self) -> dict[str, str]: ...        # Starter tools for Architect
    def custom_backpressure(self, result) -> dict: ...  # Domain-specific signals
Observation carries both structured data (for tools) and a narrative string (for Claude). Result carries scores, winner, a summary, replay data, and domain-specific metrics.
Swapping scenarios = implement this interface. Agent prompts don't change — scenario-specific content (rules, strategy template, evaluation criteria) is injected at runtime via prepare-prompt.
PrimeIntellect Environments Hub compatibility: ScenarioInterface can wrap a verifiers environment (MultiTurnEnv / SandboxEnv) and add the natural language layer Claude needs but verifiers doesn't provide. This gives MTS access to 400+ community RL environments out of the box — demo a scenario swap using a Hub environment we didn't write. The verifiers Rubric pattern (composable async reward functions returning 0.0–1.0) informs our backpressure scoring.

Agent Team
All agents are Claude Code subagents spawned via the Agent SDK. Not every agent needs the heaviest model — we tier by reasoning complexity to maximize credit efficiency.
AgentTaskOutputModelBackpressureCompetitorWrite strategy code for current scenariostrategies/{scenario}/challenger.pySonnetMust beat current best >55%. Invalid actions rejected immediately.AnalystRead replay narratives, find what worked/failedknowledge/{scenario}/analysis/gen_N.mdHaikuLLM-as-judge: recommendations must be specific, actionable, novel.CoachSynthesize analysis into updated playbook + optimize competitor prompts/contextknowledge/{scenario}/playbook.mdOpusScore delta over 2 gens. Revert if negative.ArchitectBuild tools and improve infrastructure for all agentsknowledge/{scenario}/tools/*.pyOpuspytest gate + impact measured over 3 gens.
Only Coach and Architect — which require deep reasoning about strategy and system design — run on Opus. Competitors write code from an established playbook (Sonnet). Analyst does structured extraction (Haiku). This stretches $500 in credits significantly.
Agents communicate through shared artifacts: playbook.md, analysis reports, tool library, architect changelog. All per-scenario, all human-readable, all immediately shareable with other Claude agents.
The Architect is the novel piece — it watches the whole system and asks "what information or tools are the competitors missing?" Then it writes Python utilities (threat assessment, pathfinding, pattern matching) that get added to the competitors' toolkit. The system literally builds better tools for itself.
The Coach doubles as a prompt/context optimizer — it sees which playbook + context combinations produced the best-scoring strategies and iterates on the framing, not just the content. This is DSPy-like optimization but driven by the loop rather than a separate framework.

The Loop
for each generation:
  1. COMPETE   → Tournament runner executes matches, produces replays
  2. ANALYZE   → Analyst reads replay narratives, writes structured analysis
  3. COACH     → Reads analysis + metrics, updates playbook
  4. EVOLVE    → Competitors (parallel) read playbook + tools, write new strategies
  5. ARCHITECT → (every 3rd gen) Reads everything, builds/improves tools
  6. VALIDATE  → Backpressure check — did performance improve?
Outer loop: bash generation counter (Ralph-like simplicity) dispatching to agents via claude -p over remote shell. Inner agents: Claude Agent SDK subagents for tool use, context management, and parallel spawning. prepare-prompt injects scenario content into agent templates at runtime — agent logic never changes when you swap scenarios.
Strategy execution is sandboxed — competitor-written code runs in isolated containers. Agents write arbitrary Python strategies, so sandboxing is non-negotiable. A supervisor process per match manages strategy execution, timeout enforcement, and result collection — inspired by Ramp's Open-Inspect sandbox pattern.
Two-layer sandbox architecture: PrimeIntellect Sandboxes for tournament execution — purpose-built for high-concurrency RL workloads with sub-second provisioning via a Rust-to-pod execution path, async Python SDK (execute_command, file upload/download), batch creation (bulk_wait_for_creation), and network_access=False for running untrusted strategies securely. Provision sandboxes during the EVOLVE step so containers are warm before matches start. Fly.io Machines for the persistent control plane — outer loop state, generation history, artifact storage, real SSH. Docker for local dev.
Future optimization: Pydantic Monty (pydantic-monty) — a minimal Python interpreter in Rust with 0.06ms startup and a pause-resume execution model that maps naturally to turn-based strategy execution. Currently too experimental for a hackathon demo (no classes, minimal stdlib), but the right long-term path for the fast execution tier once it stabilizes.

Scenarios
Primary demo — Grid CTF (custom tactical simulation): 20x20 grid, fog of war, 3 unit types (Scout/Soldier/Commander), terrain. Low floor (random movement is terrible) + high ceiling (coordinated multi-phase tactics). Visual improvement from Gen 1 chaos to Gen 20 coordinated play is the demo's wow moment.
Generalizability proof — Othello: Known rules, perfect information, fast execution. Shows the framework works on a fundamentally different domain with zero changes to the agents.
Real-world bridge — Market Sim (stretch): N agents trading with hidden information. Backpressure: P&L, Sharpe ratio. The evolved playbook becomes a trading strategy document.
The domain mapping extends to wargames, cybersecurity exercises, code review, negotiation — any domain with measurable outcomes and a definable strategy interface.

Shareable Artifacts
Everything the system produces is immediately consumable by other Claude agents:

Playbook — Natural-language strategy document evolved over generations. Import into any agent's context for instant domain expertise.
Tool Library — Python utilities the Architect built. Many generalize across scenarios.
Architecture Log — What infrastructure the system built for itself and why.
Skills — Workflow instructions agents wrote for themselves. When an agent fixes a failure, it updates skills/ so the next generation doesn't repeat the mistake.
The Framework — Implement ScenarioInterface (~200-500 lines), run the loop, agents do the rest.
Hub Environments — MTS scenarios can be published to the PrimeIntellect Environments Hub as verifiers-compatible packages, making them available to the broader RL community.


Tech Stack
Python 3.11+, Claude Agent SDK, Claude API (Opus 4.6 / Sonnet), PrimeIntellect Sandboxes, Bash, JS + Canvas (viz), JSON + Markdown (artifacts).

Implementation Phases

Core Framework — ScenarioInterface ABC, tournament runner, strategy executor, prompt injection. Start with CLI dispatch (claude -p) for fast iteration. Milestone: mts run --scenario grid_ctf produces results.
First Agent Loop — Competitor loop on Grid CTF. Milestone: single agent measurably improving.
Full Agent Team — Analyst + Coach + playbook feedback + LLM-as-judge backpressure. Migrate inner agents to Agent SDK subagents. Milestone: playbook evolving.
Architect + Self-Evolution — Tool-building loop, impact measurement. Milestone: system building its own tools.
Second Scenario — Othello + Market Sim (stretch). Milestone: same loop, different scenario, confirmed improvement.
Data + Tuning — Extended runs, prompt tuning, collect compelling before/after examples.
Viz + Demo — Dashboard with scenario viewer, Elo curves, playbook diffs, scenario swap. WebSocket event stream from tournament runner to dashboard for real-time generation visualization. Backup video.