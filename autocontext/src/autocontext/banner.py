"""ASCII banner for autocontext CLI and terminal surfaces.

The banner art uses the figlet 'Colossal' style, chosen for its organic,
flowing character that evokes the iterative convergence at the heart of
autocontext.  Inspired by the energy and composition of Hermes Fly.

Author: greyhaven-ai / autocontext contributors
"""

from __future__ import annotations

from functools import lru_cache
from html import escape as html_escape
from pathlib import Path

TAGLINE = "closed-loop control plane for agent improvement"
SYNC_BLOCK_START = "<!-- autocontext-banner:start -->"
SYNC_BLOCK_END = "<!-- autocontext-banner:end -->"


def _assets_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "assets"


def get_banner_path() -> Path:
    """Return the path to the plain-text banner asset file."""
    return _assets_dir() / "banner.txt"


def get_whats_new_path() -> Path:
    """Return the path to the What's New asset file."""
    return _assets_dir() / "whats_new.txt"


@lru_cache(maxsize=1)
def load_banner_art() -> str:
    """Load the canonical ASCII banner art."""
    return get_banner_path().read_text(encoding="utf-8").strip("\n")


@lru_cache(maxsize=1)
def load_whats_new() -> tuple[str, ...]:
    """Load the canonical What's New entries."""
    return tuple(
        line.strip()
        for line in get_whats_new_path().read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def banner_plain() -> str:
    """Return the full banner as plain text (no ANSI escapes)."""
    return f"{load_banner_art()}\n\n  {TAGLINE}\n"


def print_banner_rich() -> None:
    """Print the banner with Rich styling to the terminal."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text

    from autocontext import __version__

    console = Console(stderr=True)

    art = Text(load_banner_art())
    art.stylize("bold cyan")

    console.print()
    console.print(art)
    console.print()
    console.print(f"  [dim]{TAGLINE}[/dim]")
    console.print()

    # ── What's new panel ─────────────────────────────────────────────
    whats_new = load_whats_new()
    if whats_new:
        lines = Text()
        for item in whats_new:
            lines.append("  + ", style="bold green")
            lines.append(f"{item}\n", style="default")

        panel = Panel(
            lines,
            title=f"[bold]What's new in v{__version__}[/bold]",
            title_align="left",
            border_style="dim",
            padding=(0, 1),
        )
        console.print(panel)
        console.print()


def render_readme_banner_block() -> str:
    """Render the synced README hero block."""
    whats_new = "\n".join(f"- {item}" for item in load_whats_new())
    return (
        f"{SYNC_BLOCK_START}\n"
        "```\n"
        f"{load_banner_art()}\n"
        "```\n\n"
        f"> **{TAGLINE}**\n\n"
        "## What's New\n\n"
        f"{whats_new}\n"
        f"{SYNC_BLOCK_END}"
    )


def render_dashboard_banner_block() -> str:
    """Render the synced dashboard hero block."""
    whats_new = "\n".join(
        f"          <li>{html_escape(item)}</li>" for item in load_whats_new()
    )
    return (
        f"{SYNC_BLOCK_START}\n"
        '    <section class="hero">\n'
        f'      <pre class="ascii-banner">{html_escape(load_banner_art(), quote=False)}</pre>\n'
        f'      <p class="ascii-tagline">{html_escape(TAGLINE)}</p>\n'
        '      <div class="card whats-new">\n'
        "        <h2>What's New</h2>\n"
        "        <ul>\n"
        f"{whats_new}\n"
        "        </ul>\n"
        "      </div>\n"
        "    </section>\n"
        f"{SYNC_BLOCK_END}"
    )
