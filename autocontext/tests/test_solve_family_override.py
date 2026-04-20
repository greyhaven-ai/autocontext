"""AC-579 — --family CLI override for autoctx solve."""
from __future__ import annotations

import pytest
import typer

from autocontext.cli_solve import _validate_family_override
from autocontext.scenarios.families import list_families


class TestValidateFamilyOverride:
    def test_empty_string_is_accepted(self) -> None:
        # Empty string means "--family not provided"; no raise.
        _validate_family_override("")

    def test_none_is_accepted(self) -> None:
        # None is also treated as "not provided".
        _validate_family_override(None)

    def test_unknown_family_raises_typer_exit(self) -> None:
        with pytest.raises(typer.Exit) as excinfo:
            _validate_family_override("not_a_real_family")
        assert excinfo.value.exit_code == 1

    @pytest.mark.parametrize("family_name", [f.name for f in list_families()])
    def test_all_registered_families_are_accepted(self, family_name: str) -> None:
        _validate_family_override(family_name)
