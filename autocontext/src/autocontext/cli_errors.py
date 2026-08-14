"""Root-level CLI error rendering shared by every Typer subcommand."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from typing import Any

import typer
from typer import _click
from typer.core import TyperGroup


class StructuredUsageGroup(TyperGroup):
    """Keep parser failures machine-readable when structured output was requested."""

    def main(
        self,
        args: Sequence[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        windows_expand_args: bool = True,
        **extra: Any,
    ) -> Any:
        raw_args = list(args) if args is not None else sys.argv[1:]
        structured = "--json" in raw_args or "--ndjson" in raw_args
        if not structured:
            return super().main(
                args=args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=standalone_mode,
                windows_expand_args=windows_expand_args,
                **extra,
            )

        try:
            result = super().main(
                args=args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                windows_expand_args=windows_expand_args,
                **extra,
            )
        except _click.ClickException as exc:
            typer.echo(json.dumps({"error": exc.format_message()}), err=True)
            if standalone_mode:
                raise SystemExit(exc.exit_code) from exc
            raise

        if standalone_mode:
            raise SystemExit(result if isinstance(result, int) else 0)
        return result


__all__ = ["StructuredUsageGroup"]
