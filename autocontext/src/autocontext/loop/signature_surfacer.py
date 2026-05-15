"""Import-signature surfacing for local-module symbols (AC-768).

When generated code does ``from x import y``, this module statically extracts
``y``'s signature from ``x`` and emits a compact prompt block. No LLM call.

Three concerns, each independently testable:
  1. :func:`extract_symbols` — walk a Python source string for public symbols.
  2. :func:`resolve_imports` — locate referenced modules on disk.
  3. :func:`surface_signatures` — end-to-end orchestration.
  4. :func:`render_signatures` — prompt-block emission.

Sister to AC-728 contract-probes: probes verify outputs, this surfaces inputs.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SymbolKind = Literal["function", "class", "method"]


@dataclass(frozen=True, slots=True)
class Symbol:
    """A single public symbol surfaced from an imported module."""

    name: str
    kind: SymbolKind
    signature: str
    docstring_first_line: str | None
    qualified_name: str | None = None


# --- Symbol extraction -----------------------------------------------------


def _is_public(name: str) -> bool:
    return not name.startswith("_")


def _unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _format_args(args: ast.arguments) -> str:
    """Render an ``ast.arguments`` as a parameter list ``(a, b: int = 1, *c, **d)``."""
    parts: list[str] = []

    posonly = list(args.posonlyargs)
    regular = list(args.args)
    defaults = list(args.defaults)
    # Defaults align to the *tail* of (posonly + regular).
    all_positional = posonly + regular
    n_defaults = len(defaults)
    default_offset = len(all_positional) - n_defaults

    for i, a in enumerate(all_positional):
        rendered = a.arg
        if a.annotation is not None:
            rendered += f": {_unparse(a.annotation)}"
        if i >= default_offset:
            d = defaults[i - default_offset]
            rendered += f" = {_unparse(d)}"
        parts.append(rendered)
        if posonly and a is posonly[-1]:
            parts.append("/")

    if args.vararg is not None:
        rendered = f"*{args.vararg.arg}"
        if args.vararg.annotation is not None:
            rendered += f": {_unparse(args.vararg.annotation)}"
        parts.append(rendered)
    elif args.kwonlyargs:
        parts.append("*")

    for kw_arg, kw_default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        rendered = kw_arg.arg
        if kw_arg.annotation is not None:
            rendered += f": {_unparse(kw_arg.annotation)}"
        if kw_default is not None:
            rendered += f" = {_unparse(kw_default)}"
        parts.append(rendered)

    if args.kwarg is not None:
        rendered = f"**{args.kwarg.arg}"
        if args.kwarg.annotation is not None:
            rendered += f": {_unparse(args.kwarg.annotation)}"
        parts.append(rendered)

    return "(" + ", ".join(parts) + ")"


def _signature(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    sig = _format_args(func.args)
    if func.returns is not None:
        sig += f" -> {_unparse(func.returns)}"
    return sig


def _docstring_first_line(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module,
) -> str | None:
    doc = ast.get_docstring(node)
    if not doc:
        return None
    return doc.strip().splitlines()[0].strip()


def _symbol_from_func(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    kind: SymbolKind,
    qualified_name: str | None = None,
) -> Symbol:
    return Symbol(
        name=func.name,
        kind=kind,
        signature=_signature(func),
        docstring_first_line=_docstring_first_line(func),
        qualified_name=qualified_name,
    )


def extract_symbols(source: str) -> list[Symbol]:
    """Walk Python source, return public symbols (functions, classes, methods).

    Malformed source returns ``[]`` — we may run on partial code.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    out: list[Symbol] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _is_public(node.name):
                out.append(_symbol_from_func(node, kind="function"))
        elif isinstance(node, ast.ClassDef):
            if not _is_public(node.name):
                continue
            out.append(
                Symbol(
                    name=node.name,
                    kind="class",
                    signature="",
                    docstring_first_line=_docstring_first_line(node),
                    qualified_name=node.name,
                )
            )
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_public(sub.name):
                    out.append(
                        _symbol_from_func(
                            sub,
                            kind="method",
                            qualified_name=f"{node.name}.{sub.name}",
                        )
                    )
    return out


# --- Import resolution -----------------------------------------------------


def _imports(source: str) -> tuple[list[str], list[tuple[str, list[str], bool]]]:
    """Return (bare_imports, from_imports).

    bare_imports: module names from ``import x`` / ``import x.y``.
    from_imports: list of (module, [names], is_star) for each ``from x import …``.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [], []

    bare: list[str] = []
    froms: list[tuple[str, list[str], bool]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bare.append(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module is None or node.level != 0:
                continue
            star = any(alias.name == "*" for alias in node.names)
            names = [alias.name for alias in node.names if alias.name != "*"]
            froms.append((node.module.split(".", 1)[0], names, star))
    return bare, froms


def _locate(module_name: str, search_roots: Sequence[Path]) -> Path | None:
    for root in search_roots:
        candidate = root / f"{module_name}.py"
        if candidate.is_file():
            return candidate
        pkg_init = root / module_name / "__init__.py"
        if pkg_init.is_file():
            return pkg_init
    return None


def resolve_imports(source: str, search_roots: Sequence[Path]) -> dict[str, Path]:
    """Resolve local imports in ``source`` to on-disk module files.

    Stdlib and third-party imports are silently skipped (no matching file in
    the given roots).
    """
    bare, froms = _imports(source)
    out: dict[str, Path] = {}
    for name in bare:
        path = _locate(name, search_roots)
        if path is not None:
            out[name] = path
    for module, _names, _star in froms:
        path = _locate(module, search_roots)
        if path is not None:
            out[module] = path
    return out


# --- End-to-end orchestration ----------------------------------------------


def _filter_for_imports(
    module: str,
    symbols: Iterable[Symbol],
    froms: list[tuple[str, list[str], bool]],
    bare_imports: list[str],
) -> list[Symbol]:
    """Filter ``symbols`` from ``module`` to those actually imported by source."""
    # `from module import *` or `import module` → all public.
    if module in bare_imports:
        return list(symbols)
    for from_module, names, is_star in froms:
        if from_module != module:
            continue
        if is_star:
            return list(symbols)
        wanted = set(names)
        return [s for s in symbols if s.name in wanted or (s.qualified_name and s.qualified_name.split(".", 1)[0] in wanted)]
    return []


def surface_signatures(source: str, search_roots: Sequence[Path]) -> list[Symbol]:
    """Resolve local imports in ``source``, extract symbols from each module,
    filter to those actually requested, return the surfaced list."""
    bare, froms = _imports(source)
    resolved = resolve_imports(source, search_roots)

    out: list[Symbol] = []
    for module, path in resolved.items():
        try:
            module_source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        symbols = extract_symbols(module_source)
        out.extend(_filter_for_imports(module, symbols, froms, bare))
    return out


# --- Rendering -------------------------------------------------------------


def render_signatures(symbols: Sequence[Symbol]) -> str:
    """Emit a compact prompt block for the surfaced symbols."""
    if not symbols:
        return ""
    lines: list[str] = ["## Imported symbols available", ""]
    for s in symbols:
        if s.kind == "class":
            label = s.name
            sig_part = ""
        elif s.kind == "method":
            label = s.qualified_name or s.name
            sig_part = s.signature
        else:
            label = s.name
            sig_part = s.signature
        bullet = f"- `{label}{sig_part}`"
        if s.docstring_first_line:
            bullet += f" — {s.docstring_first_line}"
        lines.append(bullet)
    return "\n".join(lines)
