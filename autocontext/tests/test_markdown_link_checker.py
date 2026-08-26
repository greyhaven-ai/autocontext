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


def test_markdown_link_checker_rejects_case_mismatched_fragments(tmp_path: Path) -> None:
    (tmp_path / "guide.md").write_text(
        '# Lower heading\n\n<a id="explicit-lower"></a>\n',
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "[heading](guide.md#LOWER-HEADING)\n[explicit](guide.md#EXPLICIT-LOWER)\n",
        encoding="utf-8",
    )

    result = _run_checker(tmp_path)

    assert result.returncode == 1
    assert "guide.md#LOWER-HEADING" in result.stderr
    assert "guide.md#EXPLICIT-LOWER" in result.stderr
    assert "2 broken local link(s)" in result.stderr


def test_markdown_link_checker_matches_github_symbol_and_slug_collision_rules(tmp_path: Path) -> None:
    (tmp_path / "guide.md").write_text(
        (
            "# 😄 Emoji\n\n# Foo\n\n# Foo\n\n# Foo-1\n\n"
            "# Two  Spaces\n\n# Tab\tSeparated\n\n# No\N{NO-BREAK SPACE}Break\n\n"
            "# Encoded &amp;amp; Entity\n\n# `<Tag>`\n\n# x\N{SUPERSCRIPT TWO}y\n"
        ),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "\n".join(
            (
                "[emoji](guide.md#-emoji)",
                "[first](guide.md#foo)",
                "[duplicate](guide.md#foo-1)",
                "[literal collision](guide.md#foo-1-1)",
                "[spaces](guide.md#two--spaces)",
                "[tab](guide.md#tabseparated)",
                "[nonbreaking](guide.md#nobreak)",
                "[entity](guide.md#encoded-amp-entity)",
                "[code](guide.md#tag)",
                "[other-number](guide.md#xy)",
            )
        ),
        encoding="utf-8",
    )

    result = _run_checker(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "10 local link(s)" in result.stdout


def test_markdown_link_checker_ignores_html_anchors_inside_code(tmp_path: Path) -> None:
    (tmp_path / "guide.md").write_text(
        "~~~html\n<a id=\"not-real\"></a>\n~~~\n\n`<a name=\"also-not-real\"></a>`\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "[fenced](guide.md#not-real)\n[inline](guide.md#also-not-real)\n",
        encoding="utf-8",
    )

    result = _run_checker(tmp_path)

    assert result.returncode == 1
    assert "guide.md#not-real" in result.stderr
    assert "guide.md#also-not-real" in result.stderr
    assert "2 broken local link(s)" in result.stderr


def test_markdown_link_checker_accepts_parsed_block_and_inline_html_anchors(tmp_path: Path) -> None:
    (tmp_path / "guide.md").write_text(
        (
            '<a id="block-anchor"></a>\n\n'
            'Paragraph <span id="inline-anchor">text</span>.\n\n'
            '<a id=unquoted-anchor></a>\n\n'
            '<a id = "spaced-attribute"></a>\n'
        ),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        (
            "[block](guide.md#block-anchor)\n"
            "[inline](guide.md#inline-anchor)\n"
            "[unquoted](guide.md#unquoted-anchor)\n"
            "[spaced](guide.md#spaced-attribute)\n"
        ),
        encoding="utf-8",
    )

    result = _run_checker(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "4 local link(s)" in result.stdout


def test_markdown_link_checker_ignores_html_anchors_inside_comments_and_raw_text(tmp_path: Path) -> None:
    (tmp_path / "guide.md").write_text(
        (
            '<!-- <a id="comment-anchor"></a> -->\n\n'
            '<script>const example = \'<a id="script-anchor"></a>\';</script>\n'
        ),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "[comment](guide.md#comment-anchor)\n[script](guide.md#script-anchor)\n",
        encoding="utf-8",
    )

    result = _run_checker(tmp_path)

    assert result.returncode == 1
    assert "guide.md#comment-anchor" in result.stderr
    assert "guide.md#script-anchor" in result.stderr
    assert "2 broken local link(s)" in result.stderr
