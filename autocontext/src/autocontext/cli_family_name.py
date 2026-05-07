"""FamilyName — operator-supplied scenario-family value object (AC-738).

CLI commands that accept ``--family <name>`` to bypass the keyword
classifier resolve the operator's input through this value object so:

1. Typos like ``agent-task`` (dash) are rejected loudly with a
   ``did_you_mean`` suggestion rather than silently falling through to
   the default classifier.
2. Empty / whitespace input maps to ``None`` (i.e. "no override
   provided"), keeping the optional-flag idiom natural at call sites.
3. The set of valid family names is sourced from the registry of
   registered scenario families — no static list to drift.

Domain rule: a ``FamilyName`` instance only exists if its ``name`` is in
the registry at construction time. Callers that need to ask "is this
input valid?" can rely on the constructor; those that need to suggest
fixes get a structured exception with the closest matches.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass


class FamilyNameError(ValueError):
    """Raised when an operator-supplied family name is not a known family.

    Inherits from ``ValueError`` so callers that catch broad validation
    errors still cover this case. The message includes a ``did_you_mean``
    suggestion when one exists; otherwise it lists the full valid set.
    """


@dataclass(frozen=True, slots=True)
class FamilyName:
    """A validated scenario-family name.

    Construct via :meth:`from_user_input`; never via the raw constructor
    (which exists only so callers can hold immutable instances).
    """

    name: str

    @classmethod
    def from_user_input(cls, value: str | None) -> FamilyName | None:
        """Resolve operator input to a validated family name.

        ``None`` / empty / whitespace-only input returns ``None`` (i.e.
        "no override"). A non-empty value that is not a known family
        raises :class:`FamilyNameError` with a ``did_you_mean`` suggestion.
        """
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None

        known = _known_family_names()
        if stripped in known:
            return cls(name=stripped)

        # Case-insensitive match wins before we go to fuzzy matching, so
        # ``--family Agent_Task`` is treated as a known typo of ``agent_task``.
        lower = stripped.lower()
        for k in known:
            if k.lower() == lower:
                raise FamilyNameError(_format_did_you_mean(stripped, [k], known))

        suggestions = difflib.get_close_matches(
            stripped,
            known,
            n=3,
            cutoff=0.5,
        )
        # Also try a normalized form (dashes → underscores; collapse
        # repeated separators) so common typo classes get suggestions.
        normalized = stripped.replace("-", "_").replace(" ", "_")
        if normalized != stripped and normalized not in suggestions:
            extras = difflib.get_close_matches(
                normalized,
                known,
                n=2,
                cutoff=0.5,
            )
            for e in extras:
                if e not in suggestions:
                    suggestions.append(e)

        raise FamilyNameError(_format_did_you_mean(stripped, suggestions, known))


def _known_family_names() -> list[str]:
    """List currently-registered family names."""
    from autocontext.scenarios.families import list_families

    return [f.name for f in list_families()]


def _format_did_you_mean(
    user_input: str,
    suggestions: list[str],
    all_known: list[str],
) -> str:
    """Compose an operator-facing error message.

    When at least one close match exists, lead with "did you mean"; when
    none does, list the full set so the operator can pick from it.
    """
    if suggestions:
        if len(suggestions) == 1:
            tail = f"Did you mean {suggestions[0]!r}?"
        else:
            tail = f"Did you mean one of: {', '.join(repr(s) for s in suggestions)}?"
        return f"unknown --family {user_input!r}. {tail} (valid: {sorted(all_known)})"
    return f"unknown --family {user_input!r}. Valid: {sorted(all_known)}"
