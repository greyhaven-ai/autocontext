from __future__ import annotations

import logging
import os
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from autocontext.config.presets import apply_preset

logger = logging.getLogger(__name__)


class HarnessMode(StrEnum):
    """How the harness interacts with strategy execution."""

    NONE = "none"        # No harness intervention (existing behavior)
    FILTER = "filter"    # Enumerate valid moves, LLM selects by index
    VERIFY = "verify"    # LLM proposes, code validates, retry on invalid
    POLICY = "policy"    # Pure code strategy (alias for CODE_STRATEGIES_ENABLED)


class AppSettings(BaseModel):
    db_path: Path = Field(default=Path("runs/autocontext.sqlite3"))
    runs_root: Path = Field(default=Path("runs"))
    knowledge_root: Path = Field(default=Path("knowledge"))
    skills_root: Path = Field(default=Path("skills"))
    claude_skills_path: Path = Field(default=Path(".claude/skills"))
    executor_mode: str = Field(default="local")
    agent_provider: str = Field(default="anthropic")
    anthropic_api_key: str | None = Field(default=None)
    model_competitor: str = Field(default="claude-sonnet-4-5-20250929")
    model_analyst: str = Field(default="claude-sonnet-4-5-20250929")
    model_coach: str = Field(default="claude-opus-4-6")
    model_architect: str = Field(default="claude-opus-4-6")
    model_translator: str = Field(default="claude-sonnet-4-5-20250929")
    architect_every_n_gens: int = Field(default=3, ge=1)
    matches_per_generation: int = Field(default=3, ge=1)
    backpressure_min_delta: float = Field(default=0.005)
    scoring_backend: str = Field(
        default="elo",
        description="Tournament rating backend: 'elo' or 'glicko'",
    )
    scoring_dimension_regression_threshold: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Minimum per-dimension drop to flag as a regression in game scenario analysis",
    )
    self_play_enabled: bool = Field(
        default=False,
        description="Evaluate a fraction of tournament matches against prior advanced strategies from the same run",
    )
    self_play_pool_size: int = Field(
        default=3,
        ge=1,
        description="Max number of prior advanced strategies kept in the self-play opponent pool",
    )
    self_play_weight: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Fraction of tournament matches scheduled against self-play opponents when available",
    )
    hint_volume_enabled: bool = Field(
        default=True,
        description="Cap and rank active competitor hints instead of letting them grow without bound",
    )
    hint_volume_max_hints: int = Field(
        default=7,
        ge=1,
        description="Maximum number of active competitor hints retained at once",
    )
    hint_volume_archive_rotated: bool = Field(
        default=True,
        description="Keep rotated-out competitor hints in archived state for later recall or analysis",
    )
    evidence_freshness_enabled: bool = Field(
        default=True,
        description="Demote stale hints, lessons, and notebook context during prompt assembly",
    )
    evidence_freshness_max_age_gens: int = Field(
        default=10,
        ge=1,
        description="Maximum generation age before evidence is considered stale",
    )
    evidence_freshness_min_confidence: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="Minimum confidence for evidence to remain active in prompt context",
    )
    evidence_freshness_min_support: int = Field(
        default=1,
        ge=0,
        description="Minimum support count for evidence to remain active in prompt context",
    )
    regression_fixtures_enabled: bool = Field(
        default=True,
        description="Generate and consume regression fixtures from recurring friction evidence",
    )
    regression_fixture_min_occurrences: int = Field(
        default=2,
        ge=1,
        description="Minimum recurring friction count required before persisting a regression fixture",
    )
    prevalidation_regression_fixtures_enabled: bool = Field(
        default=True,
        description="Run persisted regression fixtures during the live prevalidation stage",
    )
    prevalidation_regression_fixture_limit: int = Field(
        default=5,
        ge=1,
        description="Maximum number of persisted regression fixtures checked in prevalidation",
    )
    holdout_enabled: bool = Field(
        default=True,
        description="Run holdout verification before advancing a generation",
    )
    holdout_seeds: int = Field(
        default=5,
        ge=1,
        description="Number of held-out seeds to evaluate before advance",
    )
    holdout_min_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Minimum acceptable mean holdout score",
    )
    holdout_max_regression_gap: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Maximum allowed regression from in-sample to holdout mean",
    )
    holdout_seed_offset: int = Field(
        default=10000,
        ge=1,
        description="Base seed offset for holdout evaluation",
    )
    holdout_family_policies: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Optional holdout policy overrides keyed by scenario family marker or name",
    )
    backpressure_mode: str = Field(default="simple")
    backpressure_plateau_window: int = Field(default=3, ge=1)
    backpressure_plateau_relaxation: float = Field(default=0.5, ge=0.0, le=1.0)
    default_generations: int = Field(default=1, ge=1)
    generation_time_budget_seconds: int = Field(
        default=0,
        ge=0,
        description="Soft stage-boundary time budget per generation in seconds (0 = unlimited)",
    )
    generation_scaffolding_budget_ratio: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="Share of generation budget reserved for scaffolding before execution begins",
    )
    generation_phase_budget_rollover_enabled: bool = Field(
        default=True,
        description="Allow unused scaffolding budget to roll over into execution budget",
    )
    seed_base: int = Field(default=1000)
    max_retries: int = Field(default=2, ge=0)
    retry_backoff_seconds: float = Field(default=0.25, ge=0)
    event_stream_path: Path = Field(default=Path("runs/events.ndjson"))
    primeintellect_api_base: str = Field(default="https://api.primeintellect.ai")
    primeintellect_api_key: str | None = Field(default=None)
    primeintellect_docker_image: str = Field(default="python:3.11-slim")
    primeintellect_cpu_cores: float = Field(default=1.0, ge=0.25)
    primeintellect_memory_gb: float = Field(default=2.0, ge=0.25)
    primeintellect_disk_size_gb: float = Field(default=5.0, ge=1.0)
    primeintellect_timeout_minutes: int = Field(default=30, ge=1)
    primeintellect_wait_attempts: int = Field(default=60, ge=1)
    primeintellect_max_retries: int = Field(default=2, ge=0)
    primeintellect_backoff_seconds: float = Field(default=0.75, ge=0)
    allow_primeintellect_fallback: bool = Field(default=True)
    local_sandbox_hardened: bool = Field(default=True)
    ablation_no_feedback: bool = Field(default=False)
    rlm_enabled: bool = Field(default=False)
    rlm_max_turns: int = Field(default=25, ge=1, le=50)
    rlm_max_stdout_chars: int = Field(default=8192, ge=1024)
    rlm_sub_model: str = Field(default="claude-haiku-4-5-20251001")
    rlm_code_timeout_seconds: float = Field(default=10.0, ge=1.0)
    rlm_backend: str = Field(default="exec", description="RLM REPL backend: 'exec' (default) or 'monty' (Monty sandbox)")
    rlm_competitor_enabled: bool = Field(default=False, description="Enable RLM REPL mode for Competitor role")
    playbook_max_versions: int = Field(default=5, ge=1)
    cross_run_inheritance: bool = Field(default=True)
    model_curator: str = Field(default="claude-opus-4-6")
    curator_enabled: bool = Field(default=True)
    curator_consolidate_every_n_gens: int = Field(default=3, ge=1)
    skill_max_lessons: int = Field(default=30, ge=1)
    # Skeptic agent (AC-324)
    skeptic_enabled: bool = Field(default=False, description="Enable skeptic/red-team review before persistence")
    model_skeptic: str = Field(default="claude-opus-4-6")
    skeptic_can_block: bool = Field(default=False, description="Allow skeptic 'block' to prevent advancement")
    agent_sdk_connect_mcp: bool = Field(default=False)
    sandbox_max_generations: int = Field(default=10, ge=1)
    use_pipeline_engine: bool = Field(default=False)
    # Monty sandbox executor
    monty_max_execution_time_seconds: float = Field(
        default=30.0, ge=1.0, description="Max wall-clock seconds for Monty sandbox execution",
    )
    monty_max_external_calls: int = Field(
        default=100, ge=10, description="Max external function calls per Monty execution",
    )
    # Code strategies (Phase 2)
    code_strategies_enabled: bool = Field(
        default=False, description="Competitor emits Python code instead of JSON params",
    )
    # Policy refinement (AC-156)
    policy_refinement_enabled: bool = Field(
        default=False, description="Refine code strategies via iterative zero-LLM evaluation",
    )
    policy_refinement_max_iterations: int = Field(default=50, ge=1)
    policy_refinement_matches_per_iteration: int = Field(default=5, ge=1)
    policy_refinement_convergence_window: int = Field(default=5, ge=2)
    policy_refinement_convergence_epsilon: float = Field(default=0.01, ge=0.0)
    policy_refinement_model: str = Field(default="")
    policy_refinement_timeout_per_match: float = Field(default=5.0, ge=0.1)
    # Meta-optimization
    audit_enabled: bool = Field(default=True)
    audit_log_path: Path = Field(default=Path("runs/audit.ndjson"))
    cost_tracking_enabled: bool = Field(default=True)
    cost_budget_limit: float | None = Field(default=None)
    cost_per_generation_limit: float = Field(
        default=0.0,
        ge=0.0,
        description="Soft USD cap per generation before optional stages and retries are throttled (0=unlimited)",
    )
    cost_throttle_above_total: float = Field(
        default=0.0,
        ge=0.0,
        description="Soft total USD threshold for throttling before the hard budget limit (0=disabled)",
    )
    cost_max_per_delta_point: float = Field(
        default=10.0,
        gt=0.0,
        description="Max USD spent per raw score delta point before retries are suppressed",
    )
    meta_profiling_enabled: bool = Field(default=False)
    meta_min_observations: int = Field(default=5, ge=1)
    # Tiered model routing
    tier_routing_enabled: bool = Field(default=False, description="Enable dynamic model tier selection")
    tier_haiku_model: str = Field(default="claude-haiku-4-5-20251001")
    tier_sonnet_model: str = Field(default="claude-sonnet-4-5-20250929")
    tier_opus_model: str = Field(default="claude-opus-4-6")
    tier_competitor_haiku_max_gen: int = Field(default=3, ge=1)
    tier_harness_aware_enabled: bool = Field(
        default=False,
        description="Allow strong harness coverage to demote competitor model tier",
    )
    tier_harness_coverage_demotion_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    # Agent task judge settings
    judge_model: str = Field(default="claude-sonnet-4-20250514")
    judge_samples: int = Field(default=1, ge=1)
    judge_temperature: float = Field(default=0.0, ge=0.0)
    # Multi-model provider settings
    judge_provider: str = Field(default="anthropic")
    judge_base_url: str | None = Field(default=None)
    judge_api_key: str | None = Field(default=None)
    # Evaluator disagreement (AC-330)
    judge_disagreement_threshold: float = Field(
        default=0.15, ge=0.0, le=1.0, description="Std dev threshold for flagging judge disagreement",
    )
    judge_bias_probes_enabled: bool = Field(
        default=False, description="Run bias probes on judge evaluations",
    )
    # Notification settings
    notify_webhook_url: str | None = Field(default=None)
    notify_on: str = Field(default="threshold_met,failure")
    # Stagnation detection
    stagnation_reset_enabled: bool = Field(
        default=False, description="Enable stagnation detection and fresh start",
    )
    stagnation_rollback_threshold: int = Field(
        default=5, ge=1, description="Consecutive rollbacks before fresh start",
    )
    stagnation_plateau_window: int = Field(
        default=5, ge=2, description="Window size for score plateau detection",
    )
    stagnation_plateau_epsilon: float = Field(
        default=0.01, ge=0.0, description="Max variance for plateau detection",
    )
    stagnation_distill_top_lessons: int = Field(
        default=5, ge=1, description="Top lessons to retain in fresh start",
    )
    # Progress JSON
    progress_json_enabled: bool = Field(default=True, description="Inject structured progress JSON into prompts")
    # Constraint prompts
    constraint_prompts_enabled: bool = Field(default=True, description="Append constraint suffixes to role prompts")
    # Context budget
    context_budget_tokens: int = Field(default=100_000, ge=0, description="Max estimated tokens for prompt context")
    # Knowledge coherence
    coherence_check_enabled: bool = Field(default=True, description="Run knowledge coherence check after persistence")
    # Strategy pre-validation
    prevalidation_enabled: bool = Field(default=False, description="Run self-play dry-run before tournament")
    prevalidation_max_retries: int = Field(
        default=2, ge=0, le=5, description="Max revision attempts on pre-validation failure",
    )
    prevalidation_dry_run_enabled: bool = Field(
        default=True, description="Run self-play dry-run match during pre-validation",
    )
    # Harness validators (Phase B P3)
    harness_validators_enabled: bool = Field(
        default=False, description="Run architect-generated harness validators before tournament",
    )
    harness_timeout_seconds: float = Field(
        default=5.0, ge=0.5, le=60.0, description="Timeout for harness code execution",
    )
    harness_inheritance_enabled: bool = Field(
        default=True, description="Inherit harness files across runs (requires harness_validators_enabled)",
    )
    harness_mode: HarnessMode = Field(
        default=HarnessMode.NONE, description="Harness interaction mode: none, filter, verify, policy",
    )
    # Probe matches (Phase 4)
    probe_matches: int = Field(default=0, ge=0, description="Probe matches before full tournament (0=disabled)")
    # Ecosystem convergence (Phase 4)
    ecosystem_convergence_enabled: bool = Field(
        default=False, description="Track playbook divergence between ecosystem phases",
    )
    ecosystem_divergence_threshold: float = Field(
        default=0.3, ge=0.0, le=1.0, description="Divergence ratio above which phases are oscillating",
    )
    ecosystem_oscillation_window: int = Field(
        default=3, ge=2, description="Consecutive high-divergence cycles to trigger lock",
    )
    # Dead-end registry (AR-2)
    dead_end_tracking_enabled: bool = Field(
        default=False, description="Track dead-end strategies that consistently fail",
    )
    dead_end_max_entries: int = Field(
        default=20, ge=1, description="Max dead-end entries before oldest are pruned",
    )
    # Research protocol (AR-3)
    protocol_enabled: bool = Field(
        default=False, description="Enable research protocol meta-document for architect steering",
    )
    # Exploration mode (AR-4)
    exploration_mode: Literal["linear", "rapid", "tree"] = Field(
        default="linear", description="Exploration mode: linear, rapid, or tree",
    )
    rapid_gens: int = Field(
        default=0, ge=0, description="Auto-transition from rapid to linear after N gens (0=manual)",
    )
    novelty_enabled: bool = Field(
        default=True,
        description="Apply a small novelty bonus to gate-time score comparisons",
    )
    novelty_weight: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Maximum novelty bonus added to raw score at gate time",
    )
    novelty_history_window: int = Field(
        default=5,
        ge=1,
        description="Number of recent completed strategies used when computing novelty",
    )
    divergent_competitor_enabled: bool = Field(
        default=True,
        description="Spawn a divergent competitor after repeated rollback streaks",
    )
    divergent_rollback_threshold: int = Field(
        default=5,
        ge=1,
        description="Consecutive rollbacks required before spawning a divergent competitor",
    )
    divergent_temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Sampling temperature for the divergent competitor branch",
    )
    multi_basin_enabled: bool = Field(
        default=False,
        description="Fork competitor generation into conservative/experimental/divergent branches when stuck",
    )
    multi_basin_trigger_rollbacks: int = Field(
        default=3,
        ge=1,
        description="Consecutive retry/rollback decisions required to trigger multi-basin exploration",
    )
    multi_basin_candidates: int = Field(
        default=3,
        ge=1,
        le=3,
        description="Number of multi-basin exploration branches to generate",
    )
    multi_basin_periodic_every_n: int = Field(
        default=0,
        ge=0,
        description="Run periodic multi-basin exploration every N generations (0 disables periodic mode)",
    )
    # Tree search (P4, activates when exploration_mode="tree")
    tree_max_hypotheses: int = Field(
        default=8, ge=1, description="Max concurrent strategy variants in tree search",
    )
    tree_sampling_temperature: float = Field(
        default=1.0, gt=0.0, description="Thompson sampling temperature for tree search",
    )
    # Session reports (AR-5)
    session_reports_enabled: bool = Field(
        default=True, description="Generate cross-session summary reports",
    )
    # Config-adaptive loop (AR-6)
    config_adaptive_enabled: bool = Field(
        default=False, description="Allow architect to propose meta-parameter tuning",
    )
    # Staged validation (AC-200)
    staged_validation_enabled: bool = Field(
        default=True, description="Use staged validation pipeline for pre-tournament checks",
    )
    # Pre-flight harness synthesis (AC-150)
    harness_preflight_enabled: bool = Field(
        default=False, description="Run pre-flight harness synthesis before generation 1",
    )
    harness_preflight_max_iterations: int = Field(
        default=30, ge=1, description="Max synthesis iterations for pre-flight",
    )
    harness_preflight_target_accuracy: float = Field(
        default=0.9, ge=0.0, le=1.0, description="Target accuracy threshold for pre-flight convergence",
    )
    harness_preflight_force: bool = Field(
        default=False, description="Force re-synthesis even if harness exists",
    )
    # Two-tier gating (AC-160)
    two_tier_gating_enabled: bool = Field(
        default=False, description="Enable two-tier validity+quality gating in tournament",
    )
    validity_max_retries: int = Field(
        default=3, ge=0, description="Max validity retries before falling through to tournament",
    )
    # Role routing (AC-204) -- "auto" or "off"
    role_routing: str = Field(default="off", description="Role routing mode: 'auto' or 'off'")
    # Per-role provider overrides (AC-184) -- empty = use AUTOCONTEXT_AGENT_PROVIDER
    competitor_provider: str = Field(default="", description="Provider override for competitor role")
    analyst_provider: str = Field(default="", description="Provider override for analyst role")
    coach_provider: str = Field(default="", description="Provider override for coach role")
    architect_provider: str = Field(default="", description="Provider override for architect role")
    # MLX local model inference (AC-182)
    mlx_model_path: str = Field(default="", description="Path to trained MLX model checkpoint directory")
    mlx_temperature: float = Field(default=0.8, ge=0.0, le=2.0, description="Sampling temperature for MLX model")
    mlx_max_tokens: int = Field(default=512, ge=1, description="Max generation tokens for MLX model")
    # OpenClaw agent adapter (AC-193)
    openclaw_runtime_kind: str = Field(
        default="factory",
        description="OpenClaw runtime kind: 'factory', 'cli', or 'http'",
    )
    openclaw_agent_factory: str = Field(
        default="",
        description="Import path to OpenClaw agent factory or class as module:callable",
    )
    openclaw_agent_command: str = Field(
        default="",
        description="Shell command for an external OpenClaw-compatible CLI runtime",
    )
    openclaw_agent_http_endpoint: str = Field(
        default="",
        description="HTTP endpoint for an external OpenClaw-compatible sidecar",
    )
    openclaw_agent_http_headers: str = Field(
        default="",
        description="JSON object of extra HTTP headers for the OpenClaw sidecar adapter",
    )
    openclaw_compatibility_version: str = Field(
        default="1.0",
        description="Compatibility version reported for the configured OpenClaw runtime",
    )
    openclaw_timeout_seconds: float = Field(default=30.0, ge=1.0, description="Timeout for OpenClaw agent execution")
    openclaw_max_retries: int = Field(default=2, ge=0, description="Max retries on OpenClaw agent failure")
    openclaw_retry_base_delay: float = Field(default=0.25, ge=0.0, description="Base delay for retry backoff")
    # OpenClaw distillation sidecar (AC-208)
    openclaw_distill_sidecar_factory: str = Field(
        default="",
        description="Import path to distillation sidecar factory as module:callable",
    )
    openclaw_distill_sidecar_command: str = Field(
        default="",
        description="Command template to launch an external distillation sidecar job",
    )
    # Provider consultation (AC-212)
    consultation_enabled: bool = Field(default=False, description="Enable provider consultation on stall/uncertainty")
    consultation_provider: str = Field(default="anthropic", description="Provider type for consultation")
    consultation_model: str = Field(default="claude-sonnet-4-20250514", description="Model for consultation calls")
    consultation_api_key: str = Field(default="", description="API key for consultation provider")
    consultation_base_url: str = Field(default="", description="Base URL for consultation provider")
    consultation_stagnation_threshold: int = Field(default=3, ge=2, description="Consecutive rollback/retry to trigger")
    consultation_cost_budget: float = Field(default=0.0, ge=0.0, description="Max USD per run (0=unlimited)")
    # Session notebook (AC-211)
    notebook_enabled: bool = Field(default=True, description="Enable session notebook feature")
    # Claude Code CLI runtime (AC-317)
    claude_model: str = Field(default="sonnet", description="Claude CLI model alias")
    claude_timeout: float = Field(default=120.0, ge=1.0, description="Claude CLI execution timeout")
    claude_tools: str | None = Field(default=None, description="Claude CLI tools override")
    claude_permission_mode: str = Field(default="bypassPermissions", description="Claude CLI permission mode")
    claude_session_persistence: bool = Field(default=False, description="Persist Claude CLI sessions across turns")
    # Pi CLI runtime (AC-223)
    pi_command: str = Field(default="pi", description="Path to Pi CLI binary")
    pi_timeout: float = Field(default=120.0, ge=1.0, description="Pi execution timeout")
    pi_workspace: str = Field(default="", description="Pi workspace directory")
    pi_model: str = Field(default="", description="Pi model override")
    # Codex CLI runtime (AC-317)
    codex_model: str = Field(default="o4-mini", description="Codex CLI model")
    codex_timeout: float = Field(default=120.0, ge=1.0, description="Codex CLI execution timeout")
    codex_workspace: str = Field(default="", description="Codex CLI working directory")
    codex_approval_mode: str = Field(default="full-auto", description="Codex CLI approval mode")
    codex_quiet: bool = Field(default=False, description="Use Codex CLI quiet mode")
    # Pi RPC spike (AC-225)
    pi_rpc_endpoint: str = Field(default="", description="Pi RPC endpoint URL")
    pi_rpc_api_key: str = Field(default="", description="Pi RPC API key")
    pi_rpc_session_persistence: bool = Field(default=True, description="Persist Pi sessions across turns")
    # Hermes CLI runtime (AC-351)
    hermes_command: str = Field(default="hermes", description="Path to Hermes CLI binary")
    hermes_model: str = Field(default="", description="Hermes model override")
    hermes_timeout: float = Field(default=120.0, ge=1.0, description="Hermes CLI execution timeout")
    hermes_workspace: str = Field(default="", description="Hermes CLI working directory")
    hermes_base_url: str = Field(default="", description="Hermes API base URL")
    hermes_api_key: str = Field(default="", description="Hermes API key")
    hermes_toolsets: str = Field(default="", description="Comma-separated Hermes toolsets (e.g. web,terminal)")
    hermes_skills: str = Field(default="", description="Hermes skill to preload (e.g. github-pr-workflow)")
    hermes_worktree: bool = Field(default=False, description="Run Hermes in isolated git worktree")
    hermes_quiet: bool = Field(default=False, description="Suppress Hermes UI chrome")
    hermes_provider: str = Field(
        default="",
        description=(
            "Hermes CLI provider override "
            "(e.g. auto, openrouter, nous, openai-codex, anthropic); "
            "ignored when hermes_base_url is set"
        ),
    )
    # OpenAI-compatible agent provider (AC-222)
    agent_base_url: str = Field(default="", description="Base URL for OpenAI-compatible agent provider")
    agent_api_key: str = Field(default="", description="API key for OpenAI-compatible agent provider")
    agent_default_model: str = Field(default="gpt-4o", description="Default model for OpenAI-compatible agent provider")
    # Trusted SSH executor (AC-213)
    ssh_host: str = Field(default="", description="SSH hostname for trusted executor")
    ssh_port: int = Field(default=22, ge=1, le=65535, description="SSH port")
    ssh_user: str = Field(default="", description="SSH user (empty = current user)")
    ssh_identity_file: str = Field(default="", description="Path to SSH private key")
    ssh_working_directory: str = Field(default="/tmp/autocontext", description="Remote working directory")
    ssh_connect_timeout: int = Field(default=10, ge=1, description="SSH connection timeout in seconds")
    ssh_command_timeout: float = Field(default=120.0, ge=1.0, description="Default SSH command timeout")
    ssh_allow_fallback: bool = Field(default=True, description="Fall back to local on SSH failure")
    # Monitor conditions (AC-209)
    monitor_enabled: bool = Field(default=True, description="Enable monitor condition engine")
    monitor_heartbeat_timeout: float = Field(default=300.0, ge=1.0, description="Default heartbeat timeout (seconds)")
    monitor_max_conditions: int = Field(default=100, ge=1, description="Max active conditions")

    @field_validator("cost_budget_limit", mode="before")
    @classmethod
    def _coerce_budget_limit(cls, v: object) -> float | None:
        """Treat 0 or empty string as None (no budget limit)."""
        if v is None or v == "":
            return None
        f = float(v)  # type: ignore[arg-type]
        return f if f > 0 else None


def load_settings() -> AppSettings:
    """Load settings from env vars and preset overrides.

    Priority: env var ``AUTOCONTEXT_<FIELD_NAME_UPPER>`` > preset > field default.
    Pydantic handles type coercion (str->int, str->bool, str->Path, etc.).
    """
    preset_name = os.getenv("AUTOCONTEXT_PRESET", "")
    preset = apply_preset(preset_name)

    kwargs: dict[str, Any] = {}
    for field_name in AppSettings.model_fields:
        env_key = f"AUTOCONTEXT_{field_name.upper()}"
        env_val = os.getenv(env_key)
        if env_val is not None:
            kwargs[field_name] = env_val
        elif field_name in preset:
            kwargs[field_name] = preset[field_name]

    settings = AppSettings(**kwargs)
    return validate_harness_mode(settings)


def validate_harness_mode(settings: AppSettings) -> AppSettings:
    """Validate harness_mode against dependent settings, falling back to NONE if invalid."""
    mode = settings.harness_mode
    if mode in (HarnessMode.FILTER, HarnessMode.VERIFY) and not settings.harness_validators_enabled:
        logger.warning(
            "harness_mode=%s requires harness_validators_enabled=true; falling back to 'none'",
            mode.value,
        )
        settings = settings.model_copy(update={"harness_mode": HarnessMode.NONE})
    if mode == HarnessMode.POLICY and not settings.code_strategies_enabled:
        logger.warning(
            "harness_mode=policy implies code_strategies_enabled=true; enabling it",
        )
        settings = settings.model_copy(update={"code_strategies_enabled": True})
    return settings
