#!/usr/bin/env python3
"""Validate repository-local Markdown paths and heading anchors.

External URLs are deliberately not fetched: this deterministic CI check owns
repository integrity, not third-party availability. Generated run/knowledge
artifacts, test fixtures/goldens, dependency trees, caches, and build outputs
are excluded by directory name.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token

_IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "build",
        "coverage",
        "dist",
        "fixtures",
        "golden",
        "htmlcov",
        "knowledge",
        "node_modules",
        "runs",
        "vendor",
    }
)
_HTML_ANCHOR_RE = re.compile(r"<(?:a|[A-Za-z][A-Za-z0-9:-]*)\b[^>]*\b(?:id|name)=[\"']([^\"']+)[\"']", re.I)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class MarkdownLink:
    source: Path
    line: int
    target: str


@dataclass(frozen=True, slots=True)
class LinkFailure:
    source: Path
    line: int
    target: str
    detail: str


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root (defaults to the parent of scripts/).",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    documents = _markdown_documents(root)
    parser_instance = MarkdownIt("commonmark")
    links: list[MarkdownLink] = []
    parsed: dict[Path, tuple[str, list[Token]]] = {}
    for document in documents:
        source = document.read_text(encoding="utf-8")
        tokens = parser_instance.parse(source)
        parsed[document] = (source, tokens)
        links.extend(_document_links(document, tokens))

    anchor_cache: dict[Path, frozenset[str]] = {}
    failures: list[LinkFailure] = []
    checked = 0
    for link in links:
        failure = _validate_link(root, link, parsed, anchor_cache, parser_instance)
        if failure is None:
            if _is_local_target(link.target):
                checked += 1
            continue
        checked += 1
        failures.append(failure)

    for failure in failures:
        source = failure.source.relative_to(root)
        print(f"{source}:{failure.line}: {failure.detail}: {failure.target}", file=sys.stderr)
    if failures:
        print(
            f"Markdown link check failed: {len(failures)} broken local link(s) among {checked} checked.",
            file=sys.stderr,
        )
        return 1
    print(f"Markdown link check passed: {checked} local link(s) across {len(documents)} Markdown files.")
    return 0


def _markdown_documents(root: Path) -> tuple[Path, ...]:
    return tuple(
        path
        for path in sorted(root.rglob("*.md"))
        if not any(part in _IGNORED_DIRECTORY_NAMES for part in path.relative_to(root).parts[:-1])
    )


def _document_links(source: Path, tokens: list[Token]) -> list[MarkdownLink]:
    links: list[MarkdownLink] = []
    for token in tokens:
        if token.type != "inline" or not token.children:
            continue
        line = token.map[0] + 1 if token.map else 1
        for child in token.children:
            target = ""
            if child.type == "link_open":
                target = child.attrGet("href") or ""
            elif child.type == "image":
                target = child.attrGet("src") or ""
            if target:
                links.append(MarkdownLink(source=source, line=line, target=target))
    return links


def _validate_link(
    root: Path,
    link: MarkdownLink,
    parsed: dict[Path, tuple[str, list[Token]]],
    anchor_cache: dict[Path, frozenset[str]],
    parser_instance: MarkdownIt,
) -> LinkFailure | None:
    target = html.unescape(link.target.strip())
    if not _is_local_target(target):
        return None
    path_text, separator, fragment = target.partition("#")
    path_text = unquote(path_text.split("?", 1)[0])
    if path_text:
        candidate = root / path_text.lstrip("/") if path_text.startswith("/") else link.source.parent / path_text
    else:
        candidate = link.source
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return LinkFailure(link.source, link.line, link.target, "local link escapes the repository")
    if not candidate.exists():
        return LinkFailure(link.source, link.line, link.target, "local path does not exist")
    if not separator or not fragment or candidate.suffix.lower() not in {".md", ".markdown"}:
        return None
    anchors = anchor_cache.get(candidate)
    if anchors is None:
        source, tokens = parsed.get(candidate, (candidate.read_text(encoding="utf-8"), []))
        if not tokens:
            tokens = parser_instance.parse(source)
        anchors = _document_anchors(source, tokens)
        anchor_cache[candidate] = anchors
    normalized_fragment = unquote(fragment).removeprefix("user-content-")
    if normalized_fragment not in anchors and normalized_fragment.lower() not in anchors:
        return LinkFailure(link.source, link.line, link.target, "Markdown heading anchor does not exist")
    return None


def _is_local_target(target: str) -> bool:
    if not target or target.startswith("//"):
        return False
    split = urlsplit(target)
    return not split.scheme and not split.netloc


def _document_anchors(source: str, tokens: list[Token]) -> frozenset[str]:
    anchors = set(_HTML_ANCHOR_RE.findall(source))
    counts: defaultdict[str, int] = defaultdict(int)
    for index, token in enumerate(tokens):
        if token.type != "heading_open" or index + 1 >= len(tokens):
            continue
        inline = tokens[index + 1]
        if inline.type != "inline":
            continue
        base = _github_heading_slug(_inline_text(inline))
        occurrence = counts[base]
        counts[base] += 1
        anchors.add(base if occurrence == 0 else f"{base}-{occurrence}")
    return frozenset(anchors)


def _inline_text(token: Token) -> str:
    if not token.children:
        return token.content
    values: list[str] = []
    for child in token.children:
        if child.type in {"text", "code_inline"}:
            values.append(child.content)
        elif child.type == "image":
            values.append(child.content)
        elif child.type == "html_inline":
            values.append(_HTML_TAG_RE.sub("", child.content))
    return "".join(values)


def _github_heading_slug(value: str) -> str:
    value = html.unescape(_HTML_TAG_RE.sub("", value)).strip().lower()
    value = "".join(
        character
        for character in value
        if character in {"-", "_"} or not unicodedata.category(character).startswith("P")
    )
    return _WHITESPACE_RE.sub("-", value)


if __name__ == "__main__":
    raise SystemExit(main())
