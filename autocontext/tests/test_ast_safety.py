"""Tests for AST safety checker."""
from __future__ import annotations

import textwrap

from autocontext.execution.ast_safety import check_ast_safety


class TestCheckAstSafetyClean:
    def test_clean_function(self) -> None:
        code = textwrap.dedent("""\
            def validate_strategy(strategy, scenario):
                if "moves" not in strategy:
                    return False, ["missing moves"]
                return True, []
        """)
        assert check_ast_safety(code) == []

    def test_arithmetic_and_builtins(self) -> None:
        code = textwrap.dedent("""\
            x = len([1, 2, 3])
            y = max(x, 10)
            z = sum(range(5))
        """)
        assert check_ast_safety(code) == []

    def test_class_definition(self) -> None:
        code = textwrap.dedent("""\
            class Validator:
                def check(self, strategy):
                    return True, []
        """)
        assert check_ast_safety(code) == []

    def test_list_comprehension(self) -> None:
        code = "result = [x * 2 for x in range(10)]"
        assert check_ast_safety(code) == []


class TestCheckAstSafetyImports:
    def test_import_blocked(self) -> None:
        violations = check_ast_safety("import os")
        assert len(violations) == 1
        assert "import" in violations[0]

    def test_from_import_blocked(self) -> None:
        violations = check_ast_safety("from pathlib import Path")
        assert len(violations) == 1
        assert "import" in violations[0]

    def test_multiple_imports_blocked(self) -> None:
        code = "import os\nimport sys\nfrom subprocess import run"
        violations = check_ast_safety(code)
        assert len(violations) == 3


class TestCheckAstSafetyDunderAttributes:
    def test_class_dunder(self) -> None:
        violations = check_ast_safety("x = obj.__class__")
        assert any("__class__" in v for v in violations)

    def test_bases_dunder(self) -> None:
        violations = check_ast_safety("x = cls.__bases__")
        assert any("__bases__" in v for v in violations)

    def test_subclasses_dunder(self) -> None:
        violations = check_ast_safety("x = cls.__subclasses__()")
        assert any("__subclasses__" in v for v in violations)

    def test_globals_dunder(self) -> None:
        violations = check_ast_safety("x = fn.__globals__")
        assert any("__globals__" in v for v in violations)

    def test_builtins_dunder(self) -> None:
        violations = check_ast_safety("x = obj.__builtins__")
        assert any("__builtins__" in v for v in violations)

    def test_code_dunder(self) -> None:
        violations = check_ast_safety("x = fn.__code__")
        assert any("__code__" in v for v in violations)

    def test_dict_dunder(self) -> None:
        violations = check_ast_safety("x = obj.__dict__")
        assert any("__dict__" in v for v in violations)

    def test_mro_dunder(self) -> None:
        violations = check_ast_safety("x = cls.__mro__")
        assert any("__mro__" in v for v in violations)


class TestCheckAstSafetyDeniedNames:
    def test_eval_blocked(self) -> None:
        # Testing that the checker detects use of the 'eval' name
        violations = check_ast_safety("x = eval('1+1')")
        assert any("eval" in v for v in violations)

    def test_compile_blocked(self) -> None:
        violations = check_ast_safety("c = compile('x=1', '<s>', 'exec')")
        assert any("compile" in v for v in violations)

    def test_getattr_blocked(self) -> None:
        violations = check_ast_safety("x = getattr(obj, 'name')")
        assert any("getattr" in v for v in violations)

    def test_setattr_blocked(self) -> None:
        violations = check_ast_safety("setattr(obj, 'x', 1)")
        assert any("setattr" in v for v in violations)

    def test_delattr_blocked(self) -> None:
        violations = check_ast_safety("delattr(obj, 'x')")
        assert any("delattr" in v for v in violations)

    def test_open_blocked(self) -> None:
        violations = check_ast_safety("f = open('file.txt')")
        assert any("open" in v for v in violations)

    def test_breakpoint_blocked(self) -> None:
        violations = check_ast_safety("breakpoint()")
        assert any("breakpoint" in v for v in violations)

    def test_globals_name_blocked(self) -> None:
        violations = check_ast_safety("g = globals()")
        assert any("globals" in v for v in violations)

    def test_locals_name_blocked(self) -> None:
        violations = check_ast_safety("l = locals()")
        assert any("locals" in v for v in violations)

    def test_vars_blocked(self) -> None:
        violations = check_ast_safety("v = vars(obj)")
        assert any("vars" in v for v in violations)

    def test_dir_blocked(self) -> None:
        violations = check_ast_safety("d = dir(obj)")
        assert any("dir" in v for v in violations)

    def test_exec_name_blocked(self) -> None:
        # The denied-names list includes 'exec'; the checker flags its use
        violations = check_ast_safety("exec('x=1')")
        assert any("exec" in v for v in violations)


class TestCheckAstSafetyNested:
    def test_nested_violation_in_function(self) -> None:
        code = textwrap.dedent("""\
            def validate_strategy(strategy, scenario):
                import os
                return True, []
        """)
        violations = check_ast_safety(code)
        assert len(violations) == 1
        assert "import" in violations[0]

    def test_nested_violation_in_class(self) -> None:
        code = textwrap.dedent("""\
            class Checker:
                def check(self):
                    return self.__class__.__bases__
        """)
        violations = check_ast_safety(code)
        assert any("__class__" in v for v in violations)
        assert any("__bases__" in v for v in violations)

    def test_multiple_violations(self) -> None:
        code = textwrap.dedent("""\
            import os
            x = eval('1')
            y = obj.__globals__
        """)
        violations = check_ast_safety(code)
        assert len(violations) >= 3


class TestCheckAstSafetySyntaxError:
    def test_syntax_error_returns_violation(self) -> None:
        violations = check_ast_safety("def f(:\n")
        assert len(violations) == 1
        assert "syntax error" in violations[0]


class TestCheckAstSafetyClassHierarchyTraversal:
    def test_full_traversal_chain(self) -> None:
        code = "().__class__.__bases__[0].__subclasses__()"
        violations = check_ast_safety(code)
        assert any("__class__" in v for v in violations)
        assert any("__bases__" in v for v in violations)
        assert any("__subclasses__" in v for v in violations)
