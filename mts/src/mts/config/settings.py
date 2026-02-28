from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field


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
    meta_profile_path: Path = Field(default=Path("runs/meta_profiles.json"))
    meta_min_observations: int = Field(default=5, ge=1)
    # Phase 7: Adaptive application
    adapt_enabled: bool = Field(default=False)
    adapt_min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    adapt_max_changes_per_cycle: int = Field(default=2, ge=0)
    adapt_dry_run: bool = Field(default=False)
    # Phase 10: Heartbeat
    heartbeat_enabled: bool = Field(default=False)
    heartbeat_stall_timeout_seconds: float = Field(default=300.0, ge=10.0)
    heartbeat_escalation_interval_seconds: float = Field(default=60.0, ge=10.0)
    heartbeat_max_restart_attempts: int = Field(default=2, ge=0)
    # Phase 8: Trust layer
    trust_enabled: bool = Field(default=False)
    trust_min_observations: int = Field(default=5, ge=1)
    trust_confidence_saturation: int = Field(default=20, ge=5)
    trust_decay_rate: float = Field(default=0.05, ge=0.0, le=1.0)
    # Phase 9: Agent identity
    identity_enabled: bool = Field(default=False)
    identity_dir: Path = Field(default=Path("knowledge/_identities"))
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


def load_settings() -> AppSettings:
    return AppSettings(
        db_path=Path(os.getenv("MTS_DB_PATH", "runs/mts.sqlite3")),
        runs_root=Path(os.getenv("MTS_RUNS_ROOT", "runs")),
        knowledge_root=Path(os.getenv("MTS_KNOWLEDGE_ROOT", "knowledge")),
        skills_root=Path(os.getenv("MTS_SKILLS_ROOT", "skills")),
        claude_skills_path=Path(os.getenv("MTS_CLAUDE_SKILLS_PATH", ".claude/skills")),
        executor_mode=os.getenv("MTS_EXECUTOR_MODE", "local"),
        agent_provider=os.getenv("MTS_AGENT_PROVIDER", "anthropic"),
        anthropic_api_key=os.getenv("MTS_ANTHROPIC_API_KEY"),
        model_competitor=os.getenv("MTS_MODEL_COMPETITOR", "claude-sonnet-4-5-20250929"),
        model_analyst=os.getenv("MTS_MODEL_ANALYST", "claude-sonnet-4-5-20250929"),
        model_coach=os.getenv("MTS_MODEL_COACH", "claude-opus-4-6"),
        model_architect=os.getenv("MTS_MODEL_ARCHITECT", "claude-opus-4-6"),
        model_translator=os.getenv("MTS_MODEL_TRANSLATOR", "claude-sonnet-4-5-20250929"),
        architect_every_n_gens=int(os.getenv("MTS_ARCHITECT_EVERY_N_GENS", "3")),
        matches_per_generation=int(os.getenv("MTS_MATCHES_PER_GENERATION", "3")),
        backpressure_min_delta=float(os.getenv("MTS_BACKPRESSURE_MIN_DELTA", "0.005")),
        backpressure_mode=os.getenv("MTS_BACKPRESSURE_MODE", "simple"),
        backpressure_plateau_window=int(os.getenv("MTS_BACKPRESSURE_PLATEAU_WINDOW", "3")),
        backpressure_plateau_relaxation=float(os.getenv("MTS_BACKPRESSURE_PLATEAU_RELAXATION", "0.5")),
        default_generations=int(os.getenv("MTS_DEFAULT_GENERATIONS", "1")),
        seed_base=int(os.getenv("MTS_SEED_BASE", "1000")),
        max_retries=int(os.getenv("MTS_MAX_RETRIES", "2")),
        retry_backoff_seconds=float(os.getenv("MTS_RETRY_BACKOFF_SECONDS", "0.25")),
        event_stream_path=Path(os.getenv("MTS_EVENT_STREAM_PATH", "runs/events.ndjson")),
        primeintellect_api_base=os.getenv("MTS_PRIMEINTELLECT_API_BASE", "https://api.primeintellect.ai"),
        primeintellect_api_key=os.getenv("MTS_PRIMEINTELLECT_API_KEY"),
        primeintellect_docker_image=os.getenv("MTS_PRIMEINTELLECT_DOCKER_IMAGE", "python:3.11-slim"),
        primeintellect_cpu_cores=float(os.getenv("MTS_PRIMEINTELLECT_CPU_CORES", "1.0")),
        primeintellect_memory_gb=float(os.getenv("MTS_PRIMEINTELLECT_MEMORY_GB", "2.0")),
        primeintellect_disk_size_gb=float(os.getenv("MTS_PRIMEINTELLECT_DISK_SIZE_GB", "5.0")),
        primeintellect_timeout_minutes=int(os.getenv("MTS_PRIMEINTELLECT_TIMEOUT_MINUTES", "30")),
        primeintellect_wait_attempts=int(os.getenv("MTS_PRIMEINTELLECT_WAIT_ATTEMPTS", "60")),
        primeintellect_max_retries=int(os.getenv("MTS_PRIMEINTELLECT_MAX_RETRIES", "2")),
        primeintellect_backoff_seconds=float(os.getenv("MTS_PRIMEINTELLECT_BACKOFF_SECONDS", "0.75")),
        allow_primeintellect_fallback=os.getenv("MTS_ALLOW_PRIMEINTELLECT_FALLBACK", "true").lower() == "true",
        local_sandbox_hardened=os.getenv("MTS_LOCAL_SANDBOX_HARDENED", "true").lower() == "true",
        ablation_no_feedback=os.getenv("MTS_ABLATION_NO_FEEDBACK", "false").lower() == "true",
        rlm_enabled=os.getenv("MTS_RLM_ENABLED", "false").lower() == "true",
        rlm_max_turns=int(os.getenv("MTS_RLM_MAX_TURNS", "25")),
        rlm_max_stdout_chars=int(os.getenv("MTS_RLM_MAX_STDOUT_CHARS", "8192")),
        rlm_sub_model=os.getenv("MTS_RLM_SUB_MODEL", "claude-haiku-4-5-20251001"),
        rlm_code_timeout_seconds=float(os.getenv("MTS_RLM_CODE_TIMEOUT_SECONDS", "10.0")),
        rlm_backend=os.getenv("MTS_RLM_BACKEND", "exec"),
        playbook_max_versions=int(os.getenv("MTS_PLAYBOOK_MAX_VERSIONS", "5")),
        cross_run_inheritance=os.getenv("MTS_CROSS_RUN_INHERITANCE", "true").lower() == "true",
        model_curator=os.getenv("MTS_MODEL_CURATOR", "claude-opus-4-6"),
        curator_enabled=os.getenv("MTS_CURATOR_ENABLED", "true").lower() == "true",
        curator_consolidate_every_n_gens=int(os.getenv("MTS_CURATOR_CONSOLIDATE_EVERY_N_GENS", "3")),
        skill_max_lessons=int(os.getenv("MTS_SKILL_MAX_LESSONS", "30")),
        agent_sdk_connect_mcp=os.getenv("MTS_AGENT_SDK_CONNECT_MCP", "false").lower() == "true",
        sandbox_max_generations=int(os.getenv("MTS_SANDBOX_MAX_GENERATIONS", "10")),
        use_pipeline_engine=os.getenv("MTS_USE_PIPELINE_ENGINE", "false").lower() == "true",
        monty_max_execution_time_seconds=float(os.getenv("MTS_MONTY_MAX_EXECUTION_TIME_SECONDS", "30.0")),
        monty_max_external_calls=int(os.getenv("MTS_MONTY_MAX_EXTERNAL_CALLS", "100")),
        code_strategies_enabled=os.getenv("MTS_CODE_STRATEGIES_ENABLED", "false").lower() == "true",
        audit_enabled=os.getenv("MTS_AUDIT_ENABLED", "true").lower() == "true",
        audit_log_path=Path(os.getenv("MTS_AUDIT_LOG_PATH", "runs/audit.ndjson")),
        cost_tracking_enabled=os.getenv("MTS_COST_TRACKING_ENABLED", "true").lower() == "true",
        cost_budget_limit=float(os.getenv("MTS_COST_BUDGET_LIMIT", "0")) or None,
        meta_profiling_enabled=os.getenv("MTS_META_PROFILING_ENABLED", "false").lower() == "true",
        meta_profile_path=Path(os.getenv("MTS_META_PROFILE_PATH", "runs/meta_profiles.json")),
        meta_min_observations=int(os.getenv("MTS_META_MIN_OBSERVATIONS", "5")),
        adapt_enabled=os.getenv("MTS_ADAPT_ENABLED", "false").lower() == "true",
        adapt_min_confidence=float(os.getenv("MTS_ADAPT_MIN_CONFIDENCE", "0.6")),
        adapt_max_changes_per_cycle=int(os.getenv("MTS_ADAPT_MAX_CHANGES_PER_CYCLE", "2")),
        adapt_dry_run=os.getenv("MTS_ADAPT_DRY_RUN", "false").lower() == "true",
        heartbeat_enabled=os.getenv("MTS_HEARTBEAT_ENABLED", "false").lower() == "true",
        heartbeat_stall_timeout_seconds=float(os.getenv("MTS_HEARTBEAT_STALL_TIMEOUT_SECONDS", "300.0")),
        heartbeat_escalation_interval_seconds=float(os.getenv("MTS_HEARTBEAT_ESCALATION_INTERVAL_SECONDS", "60.0")),
        heartbeat_max_restart_attempts=int(os.getenv("MTS_HEARTBEAT_MAX_RESTART_ATTEMPTS", "2")),
        trust_enabled=os.getenv("MTS_TRUST_ENABLED", "false").lower() == "true",
        trust_min_observations=int(os.getenv("MTS_TRUST_MIN_OBSERVATIONS", "5")),
        trust_confidence_saturation=int(os.getenv("MTS_TRUST_CONFIDENCE_SATURATION", "20")),
        trust_decay_rate=float(os.getenv("MTS_TRUST_DECAY_RATE", "0.05")),
        identity_enabled=os.getenv("MTS_IDENTITY_ENABLED", "false").lower() == "true",
        identity_dir=Path(os.getenv("MTS_IDENTITY_DIR", "knowledge/_identities")),
        judge_model=os.getenv("MTS_JUDGE_MODEL", "claude-sonnet-4-20250514"),
        judge_samples=int(os.getenv("MTS_JUDGE_SAMPLES", "1")),
        judge_temperature=float(os.getenv("MTS_JUDGE_TEMPERATURE", "0.0")),
        judge_provider=os.getenv("MTS_JUDGE_PROVIDER", "anthropic"),
        judge_base_url=os.getenv("MTS_JUDGE_BASE_URL"),
        judge_api_key=os.getenv("MTS_JUDGE_API_KEY"),
        notify_webhook_url=os.getenv("MTS_NOTIFY_WEBHOOK_URL"),
        notify_on=os.getenv("MTS_NOTIFY_ON", "threshold_met,failure"),
    )
