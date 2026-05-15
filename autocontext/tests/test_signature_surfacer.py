"""Tests for AC-768 import-signature surfacer.

Three concerns under test, each isolated:
  1. `extract_symbols`: walk a Python source string, collect public symbols.
  2. `resolve_imports`: parse imports, locate referenced module files on disk.
  3. `surface_signatures`: end-to-end orchestration.
  4. `render_signatures`: prompt-block emission.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from autocontext.loop.signature_surfacer import (
    Symbol,
    extract_symbols,
    render_signatures,
    resolve_imports,
    surface_signatures,
)


class TestExtractSymbols:
    def test_single_function_with_annotations(self) -> None:
        code = textwrap.dedent("""\
            def cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
                return b""
        """)
        symbols = extract_symbols(code)
        assert len(symbols) == 1
        s = symbols[0]
        assert s.name == "cbc_decrypt"
        assert s.kind == "function"
        assert s.signature == "(key: bytes, iv: bytes, ciphertext: bytes) -> bytes"
        assert s.docstring_first_line is None

    def test_unannotated_function(self) -> None:
        code = "def foo(x, y): return x + y"
        symbols = extract_symbols(code)
        assert len(symbols) == 1
        assert symbols[0].signature == "(x, y)"

    def test_docstring_first_line_captured(self) -> None:
        code = textwrap.dedent('''\
            def encode(data: bytes) -> str:
                """Encode bytes to base64.

                Drops padding when ``strip_padding`` is true.
                """
                return ""
        ''')
        symbols = extract_symbols(code)
        assert symbols[0].docstring_first_line == "Encode bytes to base64."

    def test_private_symbols_skipped(self) -> None:
        code = textwrap.dedent("""\
            def public_one(): pass
            def _private(): pass
            def __dunder__(): pass
        """)
        names = {s.name for s in extract_symbols(code)}
        assert names == {"public_one"}

    def test_class_with_public_methods(self) -> None:
        code = textwrap.dedent("""\
            class CBCCipher:
                def encrypt(self, plaintext: bytes) -> bytes: ...
                def decrypt(self, ciphertext: bytes) -> bytes: ...
                def _internal(self) -> None: ...
        """)
        symbols = extract_symbols(code)
        kinds = {(s.kind, s.qualified_name or s.name) for s in symbols}
        assert ("class", "CBCCipher") in kinds
        assert ("method", "CBCCipher.encrypt") in kinds
        assert ("method", "CBCCipher.decrypt") in kinds
        # private method excluded
        assert not any(s.name == "_internal" for s in symbols)

    def test_async_function(self) -> None:
        code = "async def fetch(url: str) -> bytes: return b''"
        symbols = extract_symbols(code)
        assert symbols[0].signature == "(url: str) -> bytes"

    def test_function_with_defaults(self) -> None:
        code = "def pad(data: bytes, block_size: int = 16) -> bytes: return data"
        symbols = extract_symbols(code)
        assert symbols[0].signature == "(data: bytes, block_size: int = 16) -> bytes"

    def test_function_with_star_args(self) -> None:
        code = "def f(*args: int, **kwargs: str) -> None: ..."
        symbols = extract_symbols(code)
        assert symbols[0].signature == "(*args: int, **kwargs: str) -> None"

    def test_invalid_syntax_returns_empty(self) -> None:
        # Don't blow up on malformed source — we may run on partial code.
        assert extract_symbols("def broken(:::") == []


class TestResolveImports:
    def test_from_import_resolves_to_sibling_file(self, tmp_path: Path) -> None:
        (tmp_path / "c10_cbc_mode.py").write_text("def cbc_decrypt(): pass")
        source = "from c10_cbc_mode import cbc_decrypt"
        resolved = resolve_imports(source, [tmp_path])
        assert "c10_cbc_mode" in resolved
        assert resolved["c10_cbc_mode"] == tmp_path / "c10_cbc_mode.py"

    def test_import_x_resolves(self, tmp_path: Path) -> None:
        (tmp_path / "helpers.py").write_text("")
        source = "import helpers"
        resolved = resolve_imports(source, [tmp_path])
        assert resolved["helpers"] == tmp_path / "helpers.py"

    def test_unresolvable_import_is_skipped(self, tmp_path: Path) -> None:
        # stdlib and missing modules: silently absent.
        source = "import os\nfrom hashlib import sha256\nfrom no_such_pkg import foo"
        resolved = resolve_imports(source, [tmp_path])
        assert resolved == {}

    def test_multiple_search_roots(self, tmp_path: Path) -> None:
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        (a / "alpha.py").write_text("")
        (b / "beta.py").write_text("")
        source = "from alpha import x\nfrom beta import y"
        resolved = resolve_imports(source, [a, b])
        assert set(resolved) == {"alpha", "beta"}

    def test_invalid_syntax_returns_empty(self, tmp_path: Path) -> None:
        assert resolve_imports("from :::: broken", [tmp_path]) == {}


class TestSurfaceSignatures:
    def test_end_to_end(self, tmp_path: Path) -> None:
        (tmp_path / "crypt.py").write_text(
            textwrap.dedent('''\
            def cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
                """Decrypt CBC ciphertext under (key, iv)."""
                return b""

            def _helper(): pass
        ''')
        )
        source = "from crypt import cbc_decrypt"
        surfaced = surface_signatures(source, [tmp_path])
        # Only the cbc_decrypt symbol is surfaced — _helper is private.
        names = [s.name for s in surfaced]
        assert names == ["cbc_decrypt"]
        assert surfaced[0].docstring_first_line == "Decrypt CBC ciphertext under (key, iv)."

    def test_from_import_specific_filters_to_imported_names(self, tmp_path: Path) -> None:
        (tmp_path / "many.py").write_text(
            textwrap.dedent("""\
            def needed(): pass
            def also_needed(): pass
            def unused(): pass
        """)
        )
        source = "from many import needed, also_needed"
        surfaced = surface_signatures(source, [tmp_path])
        names = {s.name for s in surfaced}
        assert names == {"needed", "also_needed"}
        # `unused` not requested; not surfaced even though it's public.

    def test_from_import_star_surfaces_all_public(self, tmp_path: Path) -> None:
        (tmp_path / "many.py").write_text(
            textwrap.dedent("""\
            def a(): pass
            def b(): pass
            def _hidden(): pass
        """)
        )
        source = "from many import *"
        surfaced = surface_signatures(source, [tmp_path])
        names = {s.name for s in surfaced}
        assert names == {"a", "b"}

    def test_no_imports_returns_empty(self, tmp_path: Path) -> None:
        assert surface_signatures("x = 1\nprint(x)", [tmp_path]) == []


class TestRefinementPromptIntegration:
    def test_signature_block_included_in_refinement_prompt(self, tmp_path: Path) -> None:
        from autocontext.loop.refinement_prompt import build_refinement_prompt

        (tmp_path / "crypt.py").write_text(
            textwrap.dedent('''\
            def cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
                """Decrypt CBC ciphertext."""
                return b""
        ''')
        )
        parent = "from crypt import cbc_decrypt\nresult = cbc_decrypt(k, ct, iv)"
        surfaced = surface_signatures(parent, [tmp_path])
        signatures_block = render_signatures(surfaced)

        prompt = build_refinement_prompt(
            scenario_rules="rules",
            strategy_interface="iface",
            evaluation_criteria="crit",
            parent_strategy=parent,
            match_feedback="wrong output",
            imported_signatures=signatures_block,
        )
        assert "## Imported symbols available" in prompt
        assert "cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes" in prompt
        assert "Decrypt CBC ciphertext." in prompt

    def test_no_signatures_means_no_block(self) -> None:
        from autocontext.loop.refinement_prompt import build_refinement_prompt

        prompt = build_refinement_prompt(
            scenario_rules="rules",
            strategy_interface="iface",
            evaluation_criteria="crit",
            parent_strategy="x = 1",
            match_feedback="wrong",
        )
        # Default empty: section header must not appear.
        assert "Imported symbols available" not in prompt


class TestRenderSignatures:
    def test_renders_compact_block(self) -> None:
        symbols = [
            Symbol(
                name="cbc_decrypt",
                kind="function",
                signature="(key: bytes, iv: bytes, ciphertext: bytes) -> bytes",
                docstring_first_line="Decrypt CBC ciphertext under (key, iv).",
            ),
            Symbol(
                name="pkcs7_pad",
                kind="function",
                signature="(data: bytes, block_size: int) -> bytes",
                docstring_first_line=None,
            ),
        ]
        block = render_signatures(symbols)
        assert "## Imported symbols available" in block
        assert "cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes" in block
        assert "Decrypt CBC ciphertext under (key, iv)." in block
        assert "pkcs7_pad(data: bytes, block_size: int) -> bytes" in block

    def test_empty_list_renders_nothing(self) -> None:
        assert render_signatures([]) == ""

    def test_methods_qualified(self) -> None:
        symbols = [
            Symbol(
                name="encrypt",
                kind="method",
                signature="(self, plaintext: bytes) -> bytes",
                docstring_first_line=None,
                qualified_name="CBCCipher.encrypt",
            ),
        ]
        assert "CBCCipher.encrypt" in render_signatures(symbols)
