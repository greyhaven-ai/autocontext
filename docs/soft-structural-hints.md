# Soft structural hints (AC-796)

Soft structural hints are opt-in. Set `AUTOCONTEXT_SOFT_HINTS_ENABLED=true` or `hint_style=structural` to ask the coach and curator to prefer constraints, invariants, verification checks, representations, and repair directions instead of route-locking solutions.

Persisted hint metadata records `hint_style`, `support_evidence`, `is_structural`, and `route_prescriptive`. A/B helpers summarize mean score, response length, novelty, rollback rate, and hint adoption rate by style.
