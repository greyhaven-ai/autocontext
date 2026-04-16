from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from autocontext.agents.orchestrator import AgentOrchestrator
from autocontext.config.settings import AppSettings
from autocontext.storage import SQLiteStore, artifact_store_from_settings

if TYPE_CHECKING:
    from autocontext.agents.llm_client import LanguageModelClient
    from autocontext.providers.base import LLMProvider


def _sqlite_from_settings(settings: AppSettings) -> SQLiteStore:
    sqlite = SQLiteStore(settings.db_path)
    sqlite.migrate(Path(__file__).resolve().parents[2] / "migrations")
    return sqlite


def _role_default_model(settings: AppSettings, role: str) -> str:
    role_models = {
        "competitor": settings.model_competitor,
        "analyst": settings.model_analyst,
        "architect": settings.model_architect,
        "coach": settings.model_coach,
        "curator": settings.model_curator,
        "translator": settings.model_translator,
    }
    return role_models.get(role) or settings.agent_default_model


def _wrap_role_client_as_provider(
    client: LanguageModelClient,
    resolved_model: str,
    *,
    role: str,
) -> tuple[LLMProvider, str]:
    from autocontext.providers.callable_wrapper import CallableProvider

    def _llm_fn(system_prompt: str, user_prompt: str) -> str:
        response = client.generate(
            model=resolved_model,
            prompt=f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt,
            max_tokens=4096,
            temperature=0.0,
            role=role,
        )
        return response.text

    return CallableProvider(_llm_fn, model_name=resolved_model), resolved_model


def resolve_role_runtime(
    settings: AppSettings,
    *,
    role: str,
    scenario_name: str = "",
    sqlite: Any | None = None,
    artifacts: Any | None = None,
    orchestrator_cls: Any = AgentOrchestrator,
) -> tuple[LLMProvider, str]:
    resolved_sqlite = sqlite if sqlite is not None else _sqlite_from_settings(settings)
    resolved_artifacts = (
        artifacts
        if artifacts is not None
        else artifact_store_from_settings(
            settings,
            enable_buffered_writes=True,
        )
    )
    orchestrator = orchestrator_cls.from_settings(settings, artifacts=resolved_artifacts, sqlite=resolved_sqlite)
    client, model = orchestrator.resolve_role_execution(
        role,
        generation=1,
        scenario_name=scenario_name,
    )
    resolved_model = model or _role_default_model(settings, role)
    return _wrap_role_client_as_provider(client, resolved_model, role=role)
