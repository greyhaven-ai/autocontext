# Playbook Approval Gate

AC-826 makes the optional human hold gate operate on the artifact that drives the next prompt: `playbook.md`.

When `require_playbook_approval` is false, curator-approved playbooks are written live as before and any stale `playbook.pending.*` proposal is cleared. When it is true, the engine writes:

- `knowledge/<scenario>/playbook.pending.md`
- `knowledge/<scenario>/playbook.pending.json`

`read_playbook` still returns the last approved `playbook.md`, so held learning cannot reach prompts before approval.

## API

- `GET /api/knowledge/{scenario}/playbook/pending` returns pending content, diff, and provenance.
- `POST /api/knowledge/{scenario}/playbook/approve` promotes pending content to `playbook.md` and clears pending files. Python also syncs lesson bullets from the approved pending playbook into `SKILL.md`.
- `POST /api/knowledge/{scenario}/playbook/reject` clears pending files without changing the approved playbook or skill lessons.

The lesson lifecycle `pending` bucket is now always empty; lesson curation is derived from approved playbook/SKILL markdown, not a structured pending lesson store.

The wire flag `require_lesson_approval` is accepted as a deprecated alias for one compatibility window; new clients should send `require_playbook_approval`.
