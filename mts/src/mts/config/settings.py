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
    playbook_max_versions: int = Field(default=5, ge=1)
    cross_run_inheritance: bool = Field(default=True)
    model_curator: str = Field(default="claude-opus-4-6")
    curator_enabled: bool = Field(default=True)
    curator_consolidate_every_n_gens: int = Field(default=3, ge=1)
    skill_max_lessons: int = Field(default=30, ge=1)
    agent_sdk_connect_mcp: bool = Field(default=False)
    sandbox_max_generations: int = Field(default=10, ge=1)
    use_pipeline_engine: bool = Field(default=False)
    use_generation_pipeline: bool = Field(default=True)


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
        playbook_max_versions=int(os.getenv("MTS_PLAYBOOK_MAX_VERSIONS", "5")),
        cross_run_inheritance=os.getenv("MTS_CROSS_RUN_INHERITANCE", "true").lower() == "true",
        model_curator=os.getenv("MTS_MODEL_CURATOR", "claude-opus-4-6"),
        curator_enabled=os.getenv("MTS_CURATOR_ENABLED", "true").lower() == "true",
        curator_consolidate_every_n_gens=int(os.getenv("MTS_CURATOR_CONSOLIDATE_EVERY_N_GENS", "3")),
        skill_max_lessons=int(os.getenv("MTS_SKILL_MAX_LESSONS", "30")),
        agent_sdk_connect_mcp=os.getenv("MTS_AGENT_SDK_CONNECT_MCP", "false").lower() == "true",
        sandbox_max_generations=int(os.getenv("MTS_SANDBOX_MAX_GENERATIONS", "10")),
        use_pipeline_engine=os.getenv("MTS_USE_PIPELINE_ENGINE", "false").lower() == "true",
        use_generation_pipeline=os.getenv("MTS_USE_GENERATION_PIPELINE", "false").lower() == "true",
    )
