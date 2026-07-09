"""Shared resolution for ERP-67 structural role isolation.

Given a role's prompt-parts split and the resolved client, decide whether to
deliver the untrusted reference as a separate user turn (capable backends) or
fall back to the exact flat prompt (unsafe split, or single-prompt / runtime-
bridge backends). Used by both the pipeline and direct execution paths so the
capability gating and flat fallback stay identical.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autocontext.prompts.templates import RolePromptParts


def resolve_role_turn(rp: RolePromptParts, client: object, *, suffix: str = "") -> tuple[str, str]:
    """Return ``(user_prompt, system)`` for a role.

    - Isolate (untrusted user turn + trusted system turn) only when the split is
      ``isolation_safe`` AND ``client`` advertises real message roles.
    - Otherwise return the exact flat prompt with an empty system turn —
      byte-identical to legacy, so nothing is reordered and no runtime system
      channel is silently dropped.

    ``suffix`` is trusted operator text appended by the caller to the flat prompt
    (e.g. the architect cadence-skip or the code-strategy instructions). It rides
    with the system turn when isolating and with the flat prompt when falling
    back, matching where it sits in the legacy prompt.
    """
    if rp.isolation_safe and getattr(client, "supports_structural_isolation", False):
        return rp.untrusted_reference, rp.system + suffix
    return rp.flat + suffix, ""
