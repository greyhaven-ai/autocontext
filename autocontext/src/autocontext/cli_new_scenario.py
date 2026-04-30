from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

import typer
from rich.table import Table

from autocontext.agents.orchestrator import AgentOrchestrator
from autocontext.cli_role_runtime import resolve_role_runtime
from autocontext.config.settings import AppSettings
from autocontext.scenarios import SCENARIO_REGISTRY
from autocontext.storage import SQLiteStore, artifact_store_from_settings

logger = logging.getLogger(__name__)


def _cli_attr(dependency_module: str, name: str) -> Any:
    return getattr(importlib.import_module(dependency_module), name)


def _get_custom_scenarios_dir() -> Path:
    """Return the default directory for scaffolded custom scenarios."""
    return Path("knowledge") / "_custom_scenarios"


def _create_family_scenario(
    *,
    family: str,
    name: str,
    description: str,
    settings: AppSettings,
) -> object:
    """Create a custom scenario through a registered family-specific pipeline."""
    from autocontext.scenarios.custom.creator_registry import FAMILY_CONFIGS, create_for_family

    if family not in FAMILY_CONFIGS:
        raise ValueError(f"Unknown family '{family}'. Known families: {', '.join(sorted(FAMILY_CONFIGS))}")

    sqlite = SQLiteStore(settings.db_path)
    sqlite.migrate(Path(__file__).resolve().parents[2] / "migrations")
    artifacts = artifact_store_from_settings(settings, enable_buffered_writes=True)
    provider, model = resolve_role_runtime(
        settings,
        role="architect",
        scenario_name=name,
        sqlite=sqlite,
        artifacts=artifacts,
        orchestrator_cls=AgentOrchestrator,
    )

    def llm_fn(system: str, user: str) -> str:
        return provider.complete(system, user, model=model).text

    return create_for_family(family, llm_fn, settings.knowledge_root).create(description, name=name)


def register_new_scenario_command(
    app: typer.Typer,
    *,
    console: Any,
    dependency_module: str = "autocontext.cli",
) -> None:
    @app.command("new-scenario")
    def new_scenario(
        list_templates: bool = typer.Option(False, "--list", help="List available templates"),
        list_families: bool = typer.Option(False, "--list-families", help="List available family pipelines"),
        template: str | None = typer.Option(None, "--template", help="Template to scaffold from"),
        family: str | None = typer.Option(None, "--family", help="Family-specific pipeline to generate from"),
        name: str | None = typer.Option(None, "--name", help="Name for the new scenario"),
        description: str | None = typer.Option(None, "--description", help="Natural-language scenario description"),
        judge_model: str | None = typer.Option(None, "--judge-model", help="Override judge model"),
        non_interactive: bool = typer.Option(False, "--non-interactive", help="Use defaults, skip prompts"),
    ) -> None:
        """Scaffold a new scenario from the template library."""
        del non_interactive
        from autocontext.scenarios.templates import TemplateLoader

        loader = TemplateLoader()

        if list_templates:
            templates = loader.list_templates()
            table = Table(title="Available Scenario Templates")
            table.add_column("Name", style="bold")
            table.add_column("Description")
            table.add_column("Output Format")
            table.add_column("Max Rounds", justify="right")
            for t in templates:
                table.add_row(t.name, t.description, t.output_format, str(t.max_rounds))
            console.print(table)
            return

        if list_families:
            from autocontext.scenarios.custom.creator_registry import FAMILY_CONFIGS

            table = Table(title="Available Scenario Family Pipelines")
            table.add_column("Family", style="bold")
            table.add_column("Spec")
            for family_name, config in sorted(FAMILY_CONFIGS.items()):
                table.add_row(family_name, config.spec_class_path.rsplit(":", 1)[-1])
            console.print(table)
            return

        if family is not None:
            if template is not None:
                console.print("[red]--template cannot be combined with --family[/red]")
                raise typer.Exit(code=1)
            if name is None:
                console.print("[red]--name is required when generating a family scenario[/red]")
                raise typer.Exit(code=1)
            if not description:
                console.print("[red]--description is required when generating a family scenario[/red]")
                raise typer.Exit(code=1)
            settings = _cli_attr(dependency_module, "load_settings")()
            try:
                _create_family_scenario(
                    family=family,
                    name=name,
                    description=description,
                    settings=settings,
                )
            except Exception as e:
                logger.debug("cli: caught Exception", exc_info=True)
                console.print(f"[red]Failed to generate scenario: {e}[/red]")
                raise typer.Exit(code=1) from None

            target_dir = settings.knowledge_root / "_custom_scenarios" / name
            console.print(f"[green]Scenario '{name}' created with family pipeline '{family}'[/green]")
            console.print(f"[dim]Files scaffolded to: {target_dir}[/dim]")
            return

        if template is None:
            console.print("[red]--template is required when not using --list[/red]")
            raise typer.Exit(code=1)
        if name is None:
            console.print("[red]--name is required when scaffolding a scenario[/red]")
            raise typer.Exit(code=1)

        try:
            loader.get_template(template)
        except KeyError:
            console.print(f"[red]Template '{template}' not found. Use --list to see available templates.[/red]")
            raise typer.Exit(code=1) from None

        overrides: dict[str, Any] = {}
        if judge_model is not None:
            overrides["judge_model"] = judge_model

        target_dir = _get_custom_scenarios_dir() / name
        try:
            loader.scaffold(template_name=template, target_dir=target_dir, overrides=overrides or None)
        except Exception as e:
            logger.debug("cli: caught Exception", exc_info=True)
            console.print(f"[red]Failed to scaffold scenario: {e}[/red]")
            raise typer.Exit(code=1) from None

        from autocontext.scenarios.custom.registry import load_all_custom_scenarios

        loaded = load_all_custom_scenarios(target_dir.parent.parent)
        registered = loaded.get(name)
        if registered is not None:
            SCENARIO_REGISTRY[name] = registered

        console.print(f"[green]Scenario '{name}' created from template '{template}'[/green]")
        console.print(f"[dim]Files scaffolded to: {target_dir}[/dim]")
        console.print("[dim]Available to agent-task tooling after scaffold/load via the custom scenario registry.[/dim]")
