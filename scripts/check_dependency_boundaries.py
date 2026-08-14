#!/usr/bin/env python3
"""Ratcheted Python and TypeScript source-import boundary checker.

Rules live in ``scripts/dependency-boundaries.json``. A listed legacy edge is
allowed only while it still exists: once removed, this check fails until the
allowance is deleted, so the baseline can shrink but cannot silently grow.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_ROOT = REPO_ROOT / "autocontext" / "src" / "autocontext"
TYPESCRIPT_ROOT = REPO_ROOT / "ts" / "src"
RULES_PATH = REPO_ROOT / "scripts" / "dependency-boundaries.json"

TS_STATIC_IMPORT_RE = re.compile(
    r"\b(?:import|export)\s+(?:type\s+)?(?:[^\"';]*?\s+from\s+)?[\"']([^\"']+)[\"']",
    re.MULTILINE,
)
TS_DYNAMIC_IMPORT_RE = re.compile(r"\bimport\(\s*[\"']([^\"']+)[\"']")


@dataclass(frozen=True, order=True)
class ImportEdge:
    runtime: str
    source: str
    line: int
    source_domain: str
    target_domain: str
    imported: str


@dataclass(frozen=True)
class LegacyEdge:
    source: str
    imported: str


@dataclass(frozen=True)
class BoundaryRule:
    runtime: str
    source_domain: str
    target_domain: str
    reason: str
    allowed: frozenset[LegacyEdge]


def _load_rules() -> list[BoundaryRule]:
    raw: Any = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schemaVersion") != 1:
        raise ValueError(f"{RULES_PATH}: expected schemaVersion 1")
    entries = raw.get("rules")
    if not isinstance(entries, list):
        raise ValueError(f"{RULES_PATH}: rules must be a list")

    rules: list[BoundaryRule] = []
    seen_pairs: set[tuple[str, str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"{RULES_PATH}: every rule must be an object")
        runtime = _required_string(entry, "runtime")
        source_domain = _required_string(entry, "sourceDomain")
        target_domain = _required_string(entry, "targetDomain")
        reason = _required_string(entry, "reason")
        pair = (runtime, source_domain, target_domain)
        if pair in seen_pairs:
            raise ValueError(f"{RULES_PATH}: duplicate rule {pair}")
        seen_pairs.add(pair)

        allowed_raw = entry.get("allowedLegacyEdges", [])
        if not isinstance(allowed_raw, list):
            raise ValueError(f"{RULES_PATH}: allowedLegacyEdges must be a list")
        allowed: set[LegacyEdge] = set()
        for legacy in allowed_raw:
            if not isinstance(legacy, dict):
                raise ValueError(f"{RULES_PATH}: legacy edge must be an object")
            allowed.add(
                LegacyEdge(
                    source=_required_string(legacy, "source"),
                    imported=_required_string(legacy, "import"),
                )
            )
        rules.append(
            BoundaryRule(
                runtime=runtime,
                source_domain=source_domain,
                target_domain=target_domain,
                reason=reason,
                allowed=frozenset(allowed),
            )
        )
    return rules


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{RULES_PATH}: {key} must be a non-empty string")
    return item


def _python_edges() -> list[ImportEdge]:
    edges: list[ImportEdge] = []
    for path in sorted(PYTHON_ROOT.rglob("*.py")):
        relative = path.relative_to(PYTHON_ROOT)
        source_domain = relative.parts[0] if len(relative.parts) > 1 else "<root>"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = _resolve_python_import(relative, node.module, node.level)
                if module is not None:
                    modules.append(module)
            elif isinstance(node, ast.Call):
                dynamic = _literal_python_import(node)
                if dynamic is not None:
                    modules.append(dynamic)
            else:
                continue

            for module in modules:
                target_domain = _python_domain(module)
                if target_domain is None or target_domain == source_domain:
                    continue
                edges.append(
                    ImportEdge(
                        runtime="python",
                        source=relative.as_posix(),
                        line=node.lineno,
                        source_domain=source_domain,
                        target_domain=target_domain,
                        imported=module,
                    )
                )
    return edges


def _resolve_python_import(relative: Path, module: str | None, level: int) -> str | None:
    if level == 0:
        return module
    module_parts = list(relative.with_suffix("").parts)
    if module_parts[-1] == "__init__":
        module_parts.pop()
    else:
        module_parts.pop()
    package = ["autocontext", *module_parts]
    climb = level - 1
    if climb > len(package) - 1:
        return None
    base = package[: len(package) - climb]
    if module:
        base.extend(module.split("."))
    return ".".join(base)


def _literal_python_import(node: ast.Call) -> str | None:
    name = ""
    if isinstance(node.func, ast.Name):
        name = node.func.id
    elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
        name = f"{node.func.value.id}.{node.func.attr}"
    if name not in {"import_module", "importlib.import_module", "__import__"}:
        return None
    if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
        return None
    return node.args[0].value


def _python_domain(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) < 2 or parts[0] != "autocontext":
        return None
    return parts[1]


def _typescript_edges() -> list[ImportEdge]:
    edges: list[ImportEdge] = []
    for path in sorted(TYPESCRIPT_ROOT.rglob("*.ts")):
        relative = path.relative_to(TYPESCRIPT_ROOT)
        source_domain = relative.parts[0] if len(relative.parts) > 1 else "<root>"
        text = path.read_text(encoding="utf-8")
        matches = [*TS_STATIC_IMPORT_RE.finditer(text), *TS_DYNAMIC_IMPORT_RE.finditer(text)]
        seen: set[tuple[int, str]] = set()
        for match in sorted(matches, key=lambda item: item.start()):
            imported = match.group(1)
            line = text.count("\n", 0, match.start()) + 1
            if (line, imported) in seen:
                continue
            seen.add((line, imported))
            target_domain = _typescript_domain(path, imported)
            if target_domain is None or target_domain == source_domain:
                continue
            edges.append(
                ImportEdge(
                    runtime="typescript",
                    source=relative.as_posix(),
                    line=line,
                    source_domain=source_domain,
                    target_domain=target_domain,
                    imported=imported,
                )
            )
    return edges


def _typescript_domain(source: Path, imported: str) -> str | None:
    if not imported.startswith("."):
        return None
    target = (source.parent / imported).resolve()
    try:
        relative = target.relative_to(TYPESCRIPT_ROOT)
    except ValueError:
        return None
    return relative.parts[0] if len(relative.parts) > 1 else "<root>"


def _check(rules: list[BoundaryRule], edges: list[ImportEdge]) -> list[str]:
    messages: list[str] = []
    for rule in rules:
        matching = [
            edge
            for edge in edges
            if edge.runtime == rule.runtime
            and edge.source_domain == rule.source_domain
            and edge.target_domain == rule.target_domain
        ]
        actual = {LegacyEdge(source=edge.source, imported=edge.imported) for edge in matching}
        for edge in matching:
            legacy = LegacyEdge(source=edge.source, imported=edge.imported)
            if legacy in rule.allowed:
                continue
            messages.append(
                f"{edge.runtime}: {edge.source}:{edge.line} imports {edge.imported!r} "
                f"({edge.source_domain} -> {edge.target_domain}); {rule.reason}"
            )
        for stale in sorted(rule.allowed - actual, key=lambda item: (item.source, item.imported)):
            messages.append(
                f"{rule.runtime}: remove stale legacy allowance "
                f"{stale.source} -> {stale.imported!r}; ratchets may only shrink"
            )
    return messages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime",
        choices=("all", "python", "typescript"),
        default="all",
        help="limit the scan to one runtime",
    )
    args = parser.parse_args(argv)

    try:
        rules = _load_rules()
        edges: list[ImportEdge] = []
        if args.runtime in {"all", "python"}:
            edges.extend(_python_edges())
        if args.runtime in {"all", "typescript"}:
            edges.extend(_typescript_edges())
        selected_rules = [rule for rule in rules if args.runtime == "all" or rule.runtime == args.runtime]
        violations = _check(selected_rules, edges)
    except (OSError, SyntaxError, ValueError, json.JSONDecodeError) as exc:
        print(f"dependency-boundary check error: {exc}", file=sys.stderr)
        return 2

    if violations:
        print("dependency-boundary violations:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        print(f"Rules: {RULES_PATH.relative_to(REPO_ROOT)}", file=sys.stderr)
        return 1
    print(f"dependency boundaries: ok ({len(selected_rules)} rules, {len(edges)} cross-domain imports scanned)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
