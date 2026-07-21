# Derived lesson curation

AC-827 makes the live markdown artifacts the lesson source of truth.

## Source of truth

Lessons are derived from:

- `knowledge/<scenario>/playbook.md` between `<!-- LESSONS_START -->` and `<!-- LESSONS_END -->`
- `skills/<scenario>-ops/SKILL.md` under `## Operational Lessons` when a lesson exists only in the skill

`lessons.json` is no longer read by prompt loading or lifecycle curation. The old `LessonStore` remains as a deprecated compatibility type, but new code should not write lesson text there.

## Lifecycle actions

`GET /api/knowledge/{scenario}/lifecycle` returns a derived view with stable hash ids and buckets:

- `active`: live markdown bullets
- `stale`: live markdown bullets annotated with `<!-- autocontext:lesson-status=stale -->`
- `pending`: always empty; playbook approval is handled by the playbook pending artifact
- `deadEnd`: entries from `dead_ends.md`

Actions mutate markdown, not a parallel store:

- `approve`: no-op validation for an existing live lesson; removes the stale marker if present
- `reject` / `curate:delete`: remove the bullet from playbook/SKILL markdown
- `curate:stale`: add the stale marker without deleting the bullet
- `curate:deadEnd`: append the lesson text to `dead_ends.md` and remove the bullet

## AC-236 metadata decision

The optional AC-236 applicability sidecar is intentionally dropped for now. The curator's consolidation and the explicit stale marker cover the current operator workflow without creating a second authoritative lesson schema.
