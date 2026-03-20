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
from xml.sax.saxutils import escape as xml_escape

TAGLINE = "closed-loop control plane for agent improvement"
SYNC_BLOCK_START = "<!-- autocontext-readme-hero:start -->"
SYNC_BLOCK_END = "<!-- autocontext-readme-hero:end -->"
WHATS_NEW_BLOCK_START = "<!-- autocontext-whats-new:start -->"
WHATS_NEW_BLOCK_END = "<!-- autocontext-whats-new:end -->"


def _assets_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "assets"


def get_banner_path() -> Path:
    """Return the path to the plain-text banner asset file."""
    return _assets_dir() / "banner.txt"


def get_whats_new_path() -> Path:
    """Return the path to the What's New asset file."""
    return _assets_dir() / "whats_new.txt"


def get_banner_svg_path() -> Path:
    """Return the path to the README-safe SVG banner asset."""
    return _assets_dir() / "banner.svg"


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


def render_banner_svg() -> str:
    """Render the canonical banner art as a scalable SVG."""
    lines = load_banner_art().splitlines()
    font_size = 20
    line_height = 28
    padding_x = 28
    padding_y = 30
    char_width = 12
    max_chars = max(len(line) for line in lines)
    width = padding_x * 2 + max_chars * char_width
    height = padding_y * 2 + len(lines) * line_height

    text_nodes = []
    for index, line in enumerate(lines):
        y = padding_y + font_size + index * line_height
        text_nodes.append(
            f'  <text x="{padding_x}" y="{y}" xml:space="preserve">{xml_escape(line)}</text>'
        )

    joined = "\n".join(text_nodes)
    font_family = (
        "ui-monospace, SFMono-Regular, SF Mono, Menlo, Monaco, Consolas, "
        "Liberation Mono, monospace"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-label="autocontext ASCII banner">\n'
        '  <rect width="100%" height="100%" rx="24" fill="#161b22"/>\n'
        f'  <g fill="#e6edf3" font-family="{font_family}" '
        f'font-size="{font_size}" font-weight="600">\n'
        f"{joined}\n"
        "  </g>\n"
        "</svg>\n"
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
    return (
        f"{SYNC_BLOCK_START}\n"
        '<p align="center">\n'
        '  <img src="autocontext/assets/banner.svg" alt="autocontext ASCII banner" />\n'
        "</p>\n\n"
        f'<p align="center"><strong>{TAGLINE}</strong></p>\n'
        f"{SYNC_BLOCK_END}"
    )


def render_readme_whats_new_block() -> str:
    """Render the synced README What's New section."""
    whats_new = "\n".join(f"- {item}" for item in load_whats_new())
    return (
        f"{WHATS_NEW_BLOCK_START}\n"
        "## What's New\n\n"
        f"{whats_new}\n"
        f"{WHATS_NEW_BLOCK_END}"
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
