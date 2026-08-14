from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_dependency_boundaries as checker


class DependencyBoundaryScannerTests(unittest.TestCase):
    def test_python_package_root_imports_resolve_imported_domains(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir).resolve()
            for domain in ("agents", "config", "knowledge", "storage"):
                (source_root / domain).mkdir()
            (source_root / "storage" / "absolute.py").write_text(
                "from autocontext import knowledge\n",
                encoding="utf-8",
            )
            (source_root / "config" / "relative.py").write_text(
                "from .. import agents\n",
                encoding="utf-8",
            )
            (source_root / "storage" / "symbol.py").write_text(
                "from autocontext import __version__\n",
                encoding="utf-8",
            )

            with patch.object(checker, "PYTHON_ROOT", source_root):
                edges = checker._python_edges()

        actual = {
            (edge.source, edge.source_domain, edge.target_domain, edge.imported)
            for edge in edges
        }
        self.assertIn(
            ("storage/absolute.py", "storage", "knowledge", "autocontext.knowledge"),
            actual,
        )
        self.assertIn(
            ("config/relative.py", "config", "agents", "autocontext.agents"),
            actual,
        )
        self.assertFalse(any(edge.imported == "autocontext.__version__" for edge in edges))

    def test_typescript_parser_includes_tsx_and_ignores_non_code_import_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir).resolve()
            providers = source_root / "providers"
            providers.mkdir()
            (providers / "reverse.tsx").write_text(
                'import type { AgentRuntime } from "../agents/types.js";\n'
                "export const view = <span />;\n",
                encoding="utf-8",
            )
            (providers / "exports.ts").write_text(
                'export { AgentRuntime } from "../agents/runtime.js";\n'
                'export type LazyAgent = import("../agents/type-query.js").Agent;\n'
                'export const load = () => import("../agents/lazy.js");\n',
                encoding="utf-8",
            )
            (providers / "noise.ts").write_text(
                '// import { Commented } from "../agents/comment.js"\n'
                'const quoted = \'export { Quoted } from "../agents/string.js"\';\n'
                'const templated = `import "../agents/template.js"`;\n'
                'const pattern = /import\\s+["\']\\.\\.\\/agents\\/regex\\.js["\']/;\n',
                encoding="utf-8",
            )

            with patch.object(checker, "TYPESCRIPT_ROOT", source_root):
                edges = checker._typescript_edges()

        actual = {(edge.source, edge.line, edge.imported) for edge in edges}
        self.assertIn(("providers/reverse.tsx", 1, "../agents/types.js"), actual)
        self.assertIn(("providers/exports.ts", 1, "../agents/runtime.js"), actual)
        self.assertIn(("providers/exports.ts", 2, "../agents/type-query.js"), actual)
        self.assertIn(("providers/exports.ts", 3, "../agents/lazy.js"), actual)
        self.assertFalse(any(edge.source == "providers/noise.ts" for edge in edges))


if __name__ == "__main__":
    unittest.main()
