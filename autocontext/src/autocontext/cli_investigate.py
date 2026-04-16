from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

import typer

from autocontext.config.settings import AppSettings

if TYPE_CHECKING:
    from rich.console import Console

    from autocontext.providers.base import LLMProvider


class InvestigationRuntimeResolver(Protocol):
    def __call__(self, settings: AppSettings, *, role: str) -> tuple[LLMProvider, str]: ...


def run_investigate_command(
    *,
    description: str,
    max_steps: int,
    hypotheses: int,
    save_as: str,
    json_output: bool,
    console: Console,
    load_settings_fn: Callable[[], AppSettings],
    resolve_investigation_runtime: InvestigationRuntimeResolver,
    write_json_stdout: Callable[[object], None],
    write_json_stderr: Callable[[str], None],
    check_json_exit: Callable[[dict[str, Any]], None],
) -> None:
    from autocontext.investigation.engine import InvestigationEngine, InvestigationRequest

    if not description:
        message = "--description is required. Run 'autoctx investigate --help' for usage."
        if json_output:
            write_json_stderr(message)
        else:
            console.print(f"[red]{message}[/red]")
        raise typer.Exit(code=1)

    settings = load_settings_fn()
    spec_provider, spec_model = resolve_investigation_runtime(settings, role="architect")
    analysis_provider, analysis_model = resolve_investigation_runtime(settings, role="analyst")

    def _spec_llm_fn(system: str, user: str) -> str:
        result = spec_provider.complete(system, user, model=spec_model)
        return result.text

    def _analysis_llm_fn(system: str, user: str) -> str:
        result = analysis_provider.complete(system, user, model=analysis_model)
        return result.text

    engine = InvestigationEngine(
        spec_llm_fn=_spec_llm_fn,
        analysis_llm_fn=_analysis_llm_fn,
        knowledge_root=settings.knowledge_root,
    )
    result = engine.run(
        InvestigationRequest(
            description=description,
            max_steps=max_steps,
            max_hypotheses=hypotheses,
            save_as=save_as or None,
        )
    )
    payload = result.to_dict()

    if json_output:
        write_json_stdout(payload)
        check_json_exit(payload)
        return

    if result.status == "failed":
        console.print(f"[red]Investigation failed:[/red] {result.error or 'unknown error'}")
        raise typer.Exit(code=1)

    console.print(f"[bold]Investigation:[/bold] {result.name}")
    console.print(f"Question: {result.question}")
    console.print("\n[dim]Hypotheses:[/dim]")
    for hypothesis in result.hypotheses:
        icon = "\u2713" if hypothesis.status == "supported" else "\u2717" if hypothesis.status == "contradicted" else "?"
        console.print(
            f"  {icon} {hypothesis.statement} "
            f"(confidence: {hypothesis.confidence:.2f}, {hypothesis.status})"
        )
    console.print(f"\nConclusion: {result.conclusion.best_explanation}")
    console.print(f"Confidence: {result.conclusion.confidence:.2f}")
    if result.unknowns:
        console.print("\n[dim]Unknowns:[/dim]")
        for unknown in result.unknowns:
            console.print(f"  - {unknown}")
    if result.recommended_next_steps:
        console.print("\n[dim]Next steps:[/dim]")
        for step in result.recommended_next_steps:
            console.print(f"  \u2192 {step}")
    console.print(f"\nArtifacts: {result.artifacts.investigation_dir}")
