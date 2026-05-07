"""Tests for FamilyName — operator-supplied scenario family value (AC-738).

The CLI accepts ``--family <name>`` to bypass the keyword classifier and
route directly to a specific scenario family. The bug: typos like
``agent-task`` (dash) silently fall through to the default classifier
behavior on some code paths. The fix: a value object that validates
against the registry of known families on construction and offers
``did_you_mean`` suggestions for typos.
"""

from __future__ import annotations

import pytest

from autocontext.cli_family_name import FamilyName, FamilyNameError

# -- Construction --


class TestFromUserInput:
    def test_known_family_is_accepted(self):
        # All registered family names should round-trip cleanly.
        from autocontext.scenarios.families import list_families

        for f in list_families():
            fam = FamilyName.from_user_input(f.name)
            assert fam is not None
            assert fam.name == f.name

    def test_empty_string_returns_none(self):
        # Empty / None mean "not provided" — caller treats this as "no override".
        assert FamilyName.from_user_input("") is None
        assert FamilyName.from_user_input(None) is None
        assert FamilyName.from_user_input("   ") is None

    def test_unknown_family_raises(self):
        with pytest.raises(FamilyNameError) as excinfo:
            FamilyName.from_user_input("not_a_real_family")
        msg = str(excinfo.value).lower()
        assert "unknown" in msg or "not_a_real_family" in str(excinfo.value)

    def test_typo_suggests_closest_match(self):
        # AC-738's user complaint: 'agent-task' silently fell through.
        # Now it must error AND suggest 'agent_task'.
        with pytest.raises(FamilyNameError) as excinfo:
            FamilyName.from_user_input("agent-task")
        msg = str(excinfo.value)
        # Suggestion is surfaced to the operator.
        assert "agent_task" in msg
        assert "did you mean" in msg.lower() or "?" in msg

    def test_close_typo_with_underscore_swap(self):
        with pytest.raises(FamilyNameError) as excinfo:
            FamilyName.from_user_input("agenttask")  # missing underscore
        msg = str(excinfo.value)
        assert "agent_task" in msg

    def test_far_input_lists_all_families(self):
        # When no close match exists, fall back to listing the full set
        # so the operator can pick.
        with pytest.raises(FamilyNameError) as excinfo:
            FamilyName.from_user_input("zzz_completely_different")
        msg = str(excinfo.value)
        # Mentions at least one valid family in the listing.
        assert "agent_task" in msg or "Valid:" in msg or "valid:" in msg


# -- Immutability --


class TestImmutability:
    def test_name_is_read_only(self):
        fam = FamilyName.from_user_input("agent_task")
        assert fam is not None
        with pytest.raises((AttributeError, TypeError)):
            fam.name = "other"  # type: ignore[misc]


# -- Equality / hashing --


class TestValueSemantics:
    def test_two_instances_with_same_name_are_equal(self):
        a = FamilyName.from_user_input("agent_task")
        b = FamilyName.from_user_input("agent_task")
        assert a == b

    def test_hashable(self):
        # Value objects should be usable as dict keys / set members.
        fam = FamilyName.from_user_input("agent_task")
        assert fam is not None
        d = {fam: 1}
        assert d[fam] == 1


# -- CLI-friendly raise: typer-compatible exception inheritance --


class TestCliFriendly:
    def test_exception_inherits_from_value_error(self):
        # Callers that catch broad ValueError still cover this case.
        assert issubclass(FamilyNameError, ValueError)


# -- Did-you-mean suggestion contract --


class TestDidYouMean:
    @pytest.mark.parametrize(
        "user_input,expected_suggestion",
        [
            ("agent-task", "agent_task"),
            ("agenttask", "agent_task"),
            ("Agent_Task", "agent_task"),  # case-insensitive matching
        ],
    )
    def test_suggestion_for_known_typos(self, user_input, expected_suggestion):
        with pytest.raises(FamilyNameError) as excinfo:
            FamilyName.from_user_input(user_input)
        assert expected_suggestion in str(excinfo.value)
