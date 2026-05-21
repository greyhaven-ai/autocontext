# Hermes `autocontext` skill — validation results (AC-711)

Static content rubric that scores the rendered SKILL.md against the
AC-711 evaluation criteria, without calling a live LLM. CI runs it
on every push.

## Why a static rubric

Live-LLM evaluation tells us how _one_ agent interpreted the skill
on _one_ sampling pass. It is non-deterministic, costs API budget,
and produces a result that's hard to debug. AC-711 needs to catch
regressions across every agent, on every commit. A content rubric
(typed predicates over the skill text) does that for free.

A predicate is a one-line `re.search` or substring check the
author can read in seconds; an LLM grading prompt is another piece
of state to keep in sync. When the skill drifts away from one of
the AC-711 criteria, the rubric tells you exactly which behavior
the text no longer supports.

## How to run

```bash
# JSON payload to stdout, markdown report to disk
uv run autoctx hermes validate-skill \
    --output docs/hermes-skill-validation-report.md --json

# Or just inspect the human-readable report inline
uv run autoctx hermes validate-skill
```

Exit code is non-zero when any case fails so CI can gate merges.

## Fixture prompts (from AC-711)

| Prompt id | Scenario                            | What an agent should infer                                          |
| --------- | ----------------------------------- | ------------------------------------------------------------------- |
| `p1`      | `evaluate_and_improve`              | Use CLI; Curator owns Hermes skill mutation                         |
| `p2`      | `export_best_as_skill`              | Use `autoctx hermes export-skill`; never touch `~/.hermes` directly |
| `p3`      | `look_at_curator_reports`           | Read-only inspect; explain privacy before any session ingest        |
| `p4`      | `use_local_mlx_to_train`            | Cover privacy first; do not mutate Hermes skills                    |
| `p5`      | `mcp_vs_cli`                        | Default CLI; MCP gated on configuration                             |
| `p6`      | `improve_curator_without_replacing` | Curator/autocontext separation; no skill mutation                   |

## Behaviors enforced

Each `ExpectedBehavior` is a typed predicate over the skill text:

| Behavior name                                        | What it checks                                                        |
| ---------------------------------------------------- | --------------------------------------------------------------------- |
| `prefers_cli_when_mcp_unconfigured`                  | Skill orders CLI ahead of MCP for the default case                    |
| `uses_mcp_only_when_configured`                      | Skill gates MCP on the environment being configured                   |
| `never_mutates_hermes_skills_for_inspect_or_train`   | Skill refuses direct mutation of `~/.hermes/skills/`                  |
| `explains_privacy_before_session_ingest`             | Skill warns about privacy / redaction in session / trajectory context |
| `documents_export_skill_path`                        | Skill names `autoctx hermes export-skill` as the install path         |
| `separates_curator_and_autocontext_responsibilities` | Skill draws a clean line between Curator and autocontext              |

## Negative tests pin teeth

Three regression tests deliberately mutilate the rendered skill
and assert the rubric catches the regression:

- Strip the CLI-first guidance → the rubric must report failures.
- Strip every privacy keyword from the rendered skill → the
  `explains_privacy_before_session_ingest` behavior must fail.
- Strip `export-skill` from the skill → the
  `documents_export_skill_path` behavior must fail.

Without these, a predicate that always returns True would pass
the positive test and we'd ship a useless rubric.

## Skill patches landed for this validation

While building the rubric, the **privacy** behavior failed against
the existing skill: there was no explicit warning that session and
trajectory imports carry raw prompts and responses. Per AC-711
deliverable ("Document any prompt/skill sections that caused wrong
behavior and patch the skill"), `skill.py` now ships a new
_Privacy Before Session and Trajectory Ingest_ section that:

- Distinguishes Curator decision reports (decision metadata, safe
  to import without redaction) from sessions / trajectories (raw
  prompts and responses).
- Names `autoctx hermes ingest-sessions` and `autoctx hermes
ingest-trajectories` as the affected commands.
- Documents `--redact standard` (default), `--redact strict`, and
  the `--redact off` opt-in marker.
- Recommends `--dry-run` first when blast radius is unclear.

When the AC-712 distribution PR lands, its committed
`skills/autocontext/SKILL.md` snapshot will need to be regenerated
against the patched renderer (`autoctx hermes export-skill
--with-references --force` from the repo root) so the AC-712 sync
invariant test stays green. The two PRs touch the skill in
complementary places, so whichever lands first triggers a rebase
for the other.

## Adding a new behavior

1. Write the predicate as a `_has_*` / `_is_*` helper at the top
   of `autocontext/hermes/skill_validation.py`.
2. Wrap it in an `ExpectedBehavior` constant near the existing
   ones.
3. Attach it to one or more `ValidationCase` rows in
   `DEFAULT_RUBRIC`.
4. Add a positive test (passes against the current skill) and a
   negative test (a mutilated skill makes the predicate fail).
5. If the current skill doesn't satisfy the predicate, patch
   `skill.py` to provide the guidance and regenerate
   `skills/autocontext/SKILL.md` via `autoctx hermes export-skill
--with-references --force`.

## Limits

- The rubric checks _text presence_, not _agent behavior_. A skill
  could include the right phrase in a misleading section and still
  pass. Counter-example tests (negative tests) reduce that risk
  but do not eliminate it.
- Predicates are case-sensitive by default; mixed-case rewrites
  may need `re.IGNORECASE`. When the rubric flags a regression
  that looks wrong, check casing before patching the skill.
- The rubric does not gate the skill on coverage of _every_
  command we ship, only on the AC-711 evaluation criteria.
  Command-level coverage stays a job for `agent-integration.md`.
