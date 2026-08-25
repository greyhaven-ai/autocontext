from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_CHECKER = Path(__file__).resolve().parents[2] / "scripts" / "check_markdown_links.py"


def _run_checker(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_CHECKER), "--root", str(root)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_markdown_link_checker_accepts_paths_anchors_references_and_exclusions(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text(
        "# Repeated heading\n\n## Repeated heading\n\n<a id=\"explicit-anchor\"></a>\n",
        encoding="utf-8",
    )
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "generated.md").write_text("[ignored](missing.md)\n", encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "\n".join(
            (
                "[guide](docs/guide.md)",
                "[heading](docs/guide.md#repeated-heading)",
                "[duplicate heading](docs/guide.md#repeated-heading-1)",
                "[explicit](docs/guide.md#explicit-anchor)",
                "[reference][guide-ref]",
                "[external](https://example.com/missing)",
                "`[code](missing.md)`",
                "",
                "[guide-ref]: docs/guide.md",
            )
        ),
        encoding="utf-8",
    )

    result = _run_checker(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "5 local link(s)" in result.stdout


def test_markdown_link_checker_reports_missing_paths_with_source_line(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("[missing](docs/missing.md)\n", encoding="utf-8")

    result = _run_checker(tmp_path)

    assert result.returncode == 1
    assert "README.md:1: local path does not exist: docs/missing.md" in result.stderr


def test_markdown_link_checker_reports_missing_heading_anchors(tmp_path: Path) -> None:
    (tmp_path / "guide.md").write_text("# Available heading\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("[missing](guide.md#missing-heading)\n", encoding="utf-8")

    result = _run_checker(tmp_path)

    assert result.returncode == 1
    assert "README.md:1: Markdown heading anchor does not exist: guide.md#missing-heading" in result.stderr
