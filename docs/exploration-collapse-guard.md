# Exploration Collapse Guard

AC-799 adds an experimental, opt-in guard for regretful-teacher and anti-exploration collapse.

Enable advisory warnings with:

```bash
AUTOCONTEXT_EXPLORATION_COLLAPSE_GUARD=true
```

Automatic mitigation stays off unless explicitly enabled:

```bash
AUTOCONTEXT_EXPLORATION_COLLAPSE_AUTO_MITIGATION=true
```

## What it checks

For each hint, playbook update, teacher signal, or pressure-mode change, compare the recent window before the change with the window after it. A warning is emitted when multiple signals move in the collapse direction:

- response length drops
- novelty/diversity drops
- entropy drops
- route repetition rises
- rollback rate rises
- score regresses

## Artifact shape

`persist_exploration_collapse_report()` writes an analytics artifact with operator-visible records:

```json
{
  "schema_version": 1,
  "advisory_only": true,
  "events": [
    {
      "event_type": "exploration_collapse_detected",
      "payload": {
        "guidance_change": {
          "change_id": "hint-set-v2",
          "generation_index": 2,
          "kind": "hint",
          "source_component": "soft_hints",
          "source_span": "hint:force-short-route"
        },
        "mitigation": "none"
      }
    }
  ]
}
```

Advisory mode never changes run behavior. If automatic mitigation is enabled, the event recommendation is to demote the associated guidance and switch to exploration-heavy sampling.
