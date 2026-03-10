from __future__ import annotations

import logging
import os
from enum import StrEnum
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field

from mts.config.presets import apply_preset

LOGGER = logging.getLogger(__name__)


class HarnessMode(StrEnum):
    """How the harness interacts with strategy execution."""

    NONE = "none"        # No harness intervention (existing behavior)
    FILTER = "filter"    # Enumerate valid moves, LLM selects by index
    VERIFY = "verify"    # LLM proposes, code validates, retry on invalid
    POLICY = "policy"    # Pure code strategy (alias for CODE_STRATEGIES_ENABLED)


class AppSettings(BaseModel):
    db_path: Path = Field(default=Path("runs/mts.sqlite3"))
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
    backpressure_mode: str = Field(default="simple")
    backpressure_plateau_window: int = Field(default=3, ge=1)
    backpressure_plateau_relaxation: float = Field(default=0.5, ge=0.0, le=1.0)
    default_generations: int = Field(default=1, ge=1)
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
    # Meta-optimization
    audit_enabled: bool = Field(default=True)
    audit_log_path: Path = Field(default=Path("runs/audit.ndjson"))
    cost_tracking_enabled: bool = Field(default=True)
    cost_budget_limit: float | None = Field(default=None)
    meta_profiling_enabled: bool = Field(default=False)
    meta_min_observations: int = Field(default=5, ge=1)
    # Tiered model routing
    tier_routing_enabled: bool = Field(default=False, description="Enable dynamic model tier selection")
    tier_haiku_model: str = Field(default="claude-haiku-4-5-20251001")
    tier_sonnet_model: str = Field(default="claude-sonnet-4-5-20250929")
    tier_opus_model: str = Field(default="claude-opus-4-6")
    tier_competitor_haiku_max_gen: int = Field(default=3, ge=1)
    # Agent task judge settings
    judge_model: str = Field(default="claude-sonnet-4-20250514")
    judge_samples: int = Field(default=1, ge=1)
    judge_temperature: float = Field(default=0.0, ge=0.0)
    # Multi-model provider settings
    judge_provider: str = Field(default="anthropic")
    judge_base_url: str | None = Field(default=None)
    judge_api_key: str | None = Field(default=None)
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


def load_settings() -> AppSettings:
    # Resolve preset overrides (env var > preset > hardcoded default)
    preset_name = os.getenv("MTS_PRESET", "")
    preset = apply_preset(preset_name)

    def _get(field: str, env_key: str, default: str) -> str:
        """Return env var if set, else preset value (as str), else default."""
        env_val = os.getenv(env_key)
        if env_val is not None:
            return env_val
        if field in preset:
            return str(preset[field])
        return default

    _TRUTHY = frozenset({"1", "true", "yes", "on"})

    def _get_bool(field: str, env_key: str, default: str) -> bool:
        """Return bool: env var if set, else preset value, else default."""
        env_val = os.getenv(env_key)
        if env_val is not None:
            return env_val.lower() in _TRUTHY
        if field in preset:
            return str(preset[field]).lower() in _TRUTHY
        return default.lower() in _TRUTHY

    settings = AppSettings(
        db_path=Path(_get("db_path", "MTS_DB_PATH", "runs/mts.sqlite3")),
        runs_root=Path(_get("runs_root", "MTS_RUNS_ROOT", "runs")),
        knowledge_root=Path(_get("knowledge_root", "MTS_KNOWLEDGE_ROOT", "knowledge")),
        skills_root=Path(_get("skills_root", "MTS_SKILLS_ROOT", "skills")),
        claude_skills_path=Path(_get("claude_skills_path", "MTS_CLAUDE_SKILLS_PATH", ".claude/skills")),
        executor_mode=_get("executor_mode", "MTS_EXECUTOR_MODE", "local"),
        agent_provider=_get("agent_provider", "MTS_AGENT_PROVIDER", "anthropic"),
        anthropic_api_key=os.getenv("MTS_ANTHROPIC_API_KEY"),
        model_competitor=_get("model_competitor", "MTS_MODEL_COMPETITOR", "claude-sonnet-4-5-20250929"),
        model_analyst=_get("model_analyst", "MTS_MODEL_ANALYST", "claude-sonnet-4-5-20250929"),
        model_coach=_get("model_coach", "MTS_MODEL_COACH", "claude-opus-4-6"),
        model_architect=_get("model_architect", "MTS_MODEL_ARCHITECT", "claude-opus-4-6"),
        model_translator=_get("model_translator", "MTS_MODEL_TRANSLATOR", "claude-sonnet-4-5-20250929"),
        architect_every_n_gens=int(_get("architect_every_n_gens", "MTS_ARCHITECT_EVERY_N_GENS", "3")),
        matches_per_generation=int(_get("matches_per_generation", "MTS_MATCHES_PER_GENERATION", "3")),
        backpressure_min_delta=float(_get("backpressure_min_delta", "MTS_BACKPRESSURE_MIN_DELTA", "0.005")),
        backpressure_mode=_get("backpressure_mode", "MTS_BACKPRESSURE_MODE", "simple"),
        backpressure_plateau_window=int(_get("backpressure_plateau_window", "MTS_BACKPRESSURE_PLATEAU_WINDOW", "3")),
        backpressure_plateau_relaxation=float(
            _get("backpressure_plateau_relaxation", "MTS_BACKPRESSURE_PLATEAU_RELAXATION", "0.5"),
        ),
        default_generations=int(_get("default_generations", "MTS_DEFAULT_GENERATIONS", "1")),
        seed_base=int(_get("seed_base", "MTS_SEED_BASE", "1000")),
        max_retries=int(_get("max_retries", "MTS_MAX_RETRIES", "2")),
        retry_backoff_seconds=float(_get("retry_backoff_seconds", "MTS_RETRY_BACKOFF_SECONDS", "0.25")),
        event_stream_path=Path(_get("event_stream_path", "MTS_EVENT_STREAM_PATH", "runs/events.ndjson")),
        primeintellect_api_base=_get("primeintellect_api_base", "MTS_PRIMEINTELLECT_API_BASE", "https://api.primeintellect.ai"),
        primeintellect_api_key=os.getenv("MTS_PRIMEINTELLECT_API_KEY"),
        primeintellect_docker_image=_get("primeintellect_docker_image", "MTS_PRIMEINTELLECT_DOCKER_IMAGE", "python:3.11-slim"),
        primeintellect_cpu_cores=float(_get("primeintellect_cpu_cores", "MTS_PRIMEINTELLECT_CPU_CORES", "1.0")),
        primeintellect_memory_gb=float(_get("primeintellect_memory_gb", "MTS_PRIMEINTELLECT_MEMORY_GB", "2.0")),
        primeintellect_disk_size_gb=float(_get("primeintellect_disk_size_gb", "MTS_PRIMEINTELLECT_DISK_SIZE_GB", "5.0")),
        primeintellect_timeout_minutes=int(
            _get("primeintellect_timeout_minutes", "MTS_PRIMEINTELLECT_TIMEOUT_MINUTES", "30"),
        ),
        primeintellect_wait_attempts=int(_get("primeintellect_wait_attempts", "MTS_PRIMEINTELLECT_WAIT_ATTEMPTS", "60")),
        primeintellect_max_retries=int(_get("primeintellect_max_retries", "MTS_PRIMEINTELLECT_MAX_RETRIES", "2")),
        primeintellect_backoff_seconds=float(
            _get("primeintellect_backoff_seconds", "MTS_PRIMEINTELLECT_BACKOFF_SECONDS", "0.75"),
        ),
        allow_primeintellect_fallback=_get_bool(
            "allow_primeintellect_fallback", "MTS_ALLOW_PRIMEINTELLECT_FALLBACK", "true",
        ),
        local_sandbox_hardened=_get_bool("local_sandbox_hardened", "MTS_LOCAL_SANDBOX_HARDENED", "true"),
        ablation_no_feedback=_get_bool("ablation_no_feedback", "MTS_ABLATION_NO_FEEDBACK", "false"),
        rlm_enabled=_get_bool("rlm_enabled", "MTS_RLM_ENABLED", "false"),
        rlm_max_turns=int(_get("rlm_max_turns", "MTS_RLM_MAX_TURNS", "25")),
        rlm_max_stdout_chars=int(_get("rlm_max_stdout_chars", "MTS_RLM_MAX_STDOUT_CHARS", "8192")),
        rlm_sub_model=_get("rlm_sub_model", "MTS_RLM_SUB_MODEL", "claude-haiku-4-5-20251001"),
        rlm_code_timeout_seconds=float(_get("rlm_code_timeout_seconds", "MTS_RLM_CODE_TIMEOUT_SECONDS", "10.0")),
        rlm_backend=_get("rlm_backend", "MTS_RLM_BACKEND", "exec"),
        rlm_competitor_enabled=_get_bool("rlm_competitor_enabled", "MTS_RLM_COMPETITOR_ENABLED", "false"),
        playbook_max_versions=int(_get("playbook_max_versions", "MTS_PLAYBOOK_MAX_VERSIONS", "5")),
        cross_run_inheritance=_get_bool("cross_run_inheritance", "MTS_CROSS_RUN_INHERITANCE", "true"),
        model_curator=_get("model_curator", "MTS_MODEL_CURATOR", "claude-opus-4-6"),
        curator_enabled=_get_bool("curator_enabled", "MTS_CURATOR_ENABLED", "true"),
        curator_consolidate_every_n_gens=int(
            _get("curator_consolidate_every_n_gens", "MTS_CURATOR_CONSOLIDATE_EVERY_N_GENS", "3"),
        ),
        skill_max_lessons=int(_get("skill_max_lessons", "MTS_SKILL_MAX_LESSONS", "30")),
        agent_sdk_connect_mcp=_get_bool("agent_sdk_connect_mcp", "MTS_AGENT_SDK_CONNECT_MCP", "false"),
        sandbox_max_generations=int(_get("sandbox_max_generations", "MTS_SANDBOX_MAX_GENERATIONS", "10")),
        use_pipeline_engine=_get_bool("use_pipeline_engine", "MTS_USE_PIPELINE_ENGINE", "false"),
        monty_max_execution_time_seconds=float(
            _get("monty_max_execution_time_seconds", "MTS_MONTY_MAX_EXECUTION_TIME_SECONDS", "30.0"),
        ),
        monty_max_external_calls=int(_get("monty_max_external_calls", "MTS_MONTY_MAX_EXTERNAL_CALLS", "100")),
        code_strategies_enabled=_get_bool("code_strategies_enabled", "MTS_CODE_STRATEGIES_ENABLED", "false"),
        audit_enabled=_get_bool("audit_enabled", "MTS_AUDIT_ENABLED", "true"),
        audit_log_path=Path(_get("audit_log_path", "MTS_AUDIT_LOG_PATH", "runs/audit.ndjson")),
        cost_tracking_enabled=_get_bool("cost_tracking_enabled", "MTS_COST_TRACKING_ENABLED", "true"),
        cost_budget_limit=float(_get("cost_budget_limit", "MTS_COST_BUDGET_LIMIT", "0")) or None,
        meta_profiling_enabled=_get_bool("meta_profiling_enabled", "MTS_META_PROFILING_ENABLED", "false"),
        meta_min_observations=int(_get("meta_min_observations", "MTS_META_MIN_OBSERVATIONS", "5")),
        tier_routing_enabled=_get_bool("tier_routing_enabled", "MTS_TIER_ROUTING_ENABLED", "false"),
        tier_haiku_model=_get("tier_haiku_model", "MTS_TIER_HAIKU_MODEL", "claude-haiku-4-5-20251001"),
        tier_sonnet_model=_get("tier_sonnet_model", "MTS_TIER_SONNET_MODEL", "claude-sonnet-4-5-20250929"),
        tier_opus_model=_get("tier_opus_model", "MTS_TIER_OPUS_MODEL", "claude-opus-4-6"),
        tier_competitor_haiku_max_gen=int(_get("tier_competitor_haiku_max_gen", "MTS_TIER_COMPETITOR_HAIKU_MAX_GEN", "3")),
        judge_model=_get("judge_model", "MTS_JUDGE_MODEL", "claude-sonnet-4-20250514"),
        judge_samples=int(_get("judge_samples", "MTS_JUDGE_SAMPLES", "1")),
        judge_temperature=float(_get("judge_temperature", "MTS_JUDGE_TEMPERATURE", "0.0")),
        judge_provider=_get("judge_provider", "MTS_JUDGE_PROVIDER", "anthropic"),
        judge_base_url=os.getenv("MTS_JUDGE_BASE_URL"),
        judge_api_key=os.getenv("MTS_JUDGE_API_KEY"),
        notify_webhook_url=os.getenv("MTS_NOTIFY_WEBHOOK_URL"),
        notify_on=_get("notify_on", "MTS_NOTIFY_ON", "threshold_met,failure"),
        stagnation_reset_enabled=_get_bool("stagnation_reset_enabled", "MTS_STAGNATION_RESET_ENABLED", "false"),
        stagnation_rollback_threshold=int(
            _get("stagnation_rollback_threshold", "MTS_STAGNATION_ROLLBACK_THRESHOLD", "5"),
        ),
        stagnation_plateau_window=int(_get("stagnation_plateau_window", "MTS_STAGNATION_PLATEAU_WINDOW", "5")),
        stagnation_plateau_epsilon=float(_get("stagnation_plateau_epsilon", "MTS_STAGNATION_PLATEAU_EPSILON", "0.01")),
        stagnation_distill_top_lessons=int(
            _get("stagnation_distill_top_lessons", "MTS_STAGNATION_DISTILL_TOP_LESSONS", "5"),
        ),
        progress_json_enabled=_get_bool("progress_json_enabled", "MTS_PROGRESS_JSON_ENABLED", "true"),
        constraint_prompts_enabled=_get_bool("constraint_prompts_enabled", "MTS_CONSTRAINT_PROMPTS_ENABLED", "true"),
        context_budget_tokens=int(_get("context_budget_tokens", "MTS_CONTEXT_BUDGET_TOKENS", "100000")),
        coherence_check_enabled=_get_bool("coherence_check_enabled", "MTS_COHERENCE_CHECK_ENABLED", "true"),
        prevalidation_enabled=_get_bool("prevalidation_enabled", "MTS_PREVALIDATION_ENABLED", "false"),
        prevalidation_max_retries=int(
            _get("prevalidation_max_retries", "MTS_PREVALIDATION_MAX_RETRIES", "2"),
        ),
        prevalidation_dry_run_enabled=_get_bool(
            "prevalidation_dry_run_enabled", "MTS_PREVALIDATION_DRY_RUN_ENABLED", "true",
        ),
        harness_validators_enabled=_get_bool(
            "harness_validators_enabled", "MTS_HARNESS_VALIDATORS_ENABLED", "false",
        ),
        harness_timeout_seconds=float(
            _get("harness_timeout_seconds", "MTS_HARNESS_TIMEOUT_SECONDS", "5.0"),
        ),
        harness_inheritance_enabled=_get_bool(
            "harness_inheritance_enabled", "MTS_HARNESS_INHERITANCE_ENABLED", "true",
        ),
        harness_mode=HarnessMode(_get("harness_mode", "MTS_HARNESS_MODE", "none")),
        probe_matches=int(_get("probe_matches", "MTS_PROBE_MATCHES", "0")),
        ecosystem_convergence_enabled=_get_bool(
            "ecosystem_convergence_enabled", "MTS_ECOSYSTEM_CONVERGENCE_ENABLED", "false",
        ),
        ecosystem_divergence_threshold=float(
            _get("ecosystem_divergence_threshold", "MTS_ECOSYSTEM_DIVERGENCE_THRESHOLD", "0.3"),
        ),
        ecosystem_oscillation_window=int(
            _get("ecosystem_oscillation_window", "MTS_ECOSYSTEM_OSCILLATION_WINDOW", "3"),
        ),
        dead_end_tracking_enabled=_get_bool(
            "dead_end_tracking_enabled", "MTS_DEAD_END_TRACKING_ENABLED", "false",
        ),
        dead_end_max_entries=int(_get("dead_end_max_entries", "MTS_DEAD_END_MAX_ENTRIES", "20")),
        protocol_enabled=_get_bool("protocol_enabled", "MTS_PROTOCOL_ENABLED", "false"),
        exploration_mode=cast(Literal["linear", "rapid", "tree"], _get("exploration_mode", "MTS_EXPLORATION_MODE", "linear")),
        rapid_gens=int(_get("rapid_gens", "MTS_RAPID_GENS", "0")),
        tree_max_hypotheses=int(_get("tree_max_hypotheses", "MTS_TREE_MAX_HYPOTHESES", "8")),
        tree_sampling_temperature=float(
            _get("tree_sampling_temperature", "MTS_TREE_SAMPLING_TEMPERATURE", "1.0"),
        ),
        session_reports_enabled=_get_bool("session_reports_enabled", "MTS_SESSION_REPORTS_ENABLED", "true"),
        config_adaptive_enabled=_get_bool("config_adaptive_enabled", "MTS_CONFIG_ADAPTIVE_ENABLED", "false"),
    )
    return validate_harness_mode(settings)


def validate_harness_mode(settings: AppSettings) -> AppSettings:
    """Validate harness_mode against dependent settings, falling back to NONE if invalid."""
    mode = settings.harness_mode
    if mode in (HarnessMode.FILTER, HarnessMode.VERIFY) and not settings.harness_validators_enabled:
        LOGGER.warning(
            "harness_mode=%s requires harness_validators_enabled=true; falling back to 'none'",
            mode.value,
        )
        settings = settings.model_copy(update={"harness_mode": HarnessMode.NONE})
    if mode == HarnessMode.POLICY and not settings.code_strategies_enabled:
        LOGGER.warning(
            "harness_mode=policy implies code_strategies_enabled=true; enabling it",
        )
        settings = settings.model_copy(update={"code_strategies_enabled": True})
    return settings
