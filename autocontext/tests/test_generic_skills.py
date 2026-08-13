"""AC-925: the host-agnostic creator and consumer skills.

Requested externally (GitHub #1251). The shipped `autocontext` skill is written
for Hermes and mixes producing knowledge with consuming it, so an agent on any
other host reads instructions it cannot follow, and an agent that only wants to
*read* knowledge loads the whole run-and-train surface to find four commands.

The tests that matter here are not "does the file exist". They are:

* the split is real -- neither skill teaches the other's job;
* nothing mentions a host, which is the entire request;
* every command shown is a real command with real flags, checked against the
  CLI rather than against my memory of it. A skill that documents a flag that
  does not exist is worse than no skill, because the agent trusts it;
* the committed files match what the renderer emits, so they cannot drift the
  way a hand-maintained copy would.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autocontext.skills.generic import (
    AUTOCONTEXT_CONSUMER_SKILL_NAME,
    AUTOCONTEXT_CREATOR_SKILL_NAME,
    GENERIC_SKILL_RENDERERS,
    render_consumer_skill,
    render_creator_skill,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "skills"

CREATOR = render_creator_skill()
CONSUMER = render_consumer_skill()


# --- The request: host-agnostic ------------------------------------------


@pytest.mark.parametrize("skill", [CREATOR, CONSUMER], ids=["creator", "consumer"])
def test_no_host_is_named(skill: str) -> None:
    """The whole point of the issue.

    The existing skill opens "Use when a Hermes agent needs to..." and spends
    sections on Curator and `~/.hermes`. These must work for anyone.
    """
    lowered = skill.lower()
    for host in ("hermes", "curator", "claude code", "cursor", "openclaw"):
        assert host not in lowered, f"{host!r} leaked into a host-agnostic skill"


@pytest.mark.parametrize("skill", [CREATOR, CONSUMER], ids=["creator", "consumer"])
def test_has_valid_frontmatter(skill: str) -> None:
    """Same frontmatter shape as the existing skill, so hosts can load it."""
    assert skill.startswith("---\n")
    frontmatter = skill.split("---", 2)[1]
    for field in ("name:", "description:", "version:", "author:", "license:"):
        assert field in frontmatter


# --- The split is real ----------------------------------------------------


def test_creator_does_not_teach_consumption() -> None:
    """Otherwise the split is cosmetic and the consumer skill is pointless."""
    assert "autocontext-consumer" in CREATOR, "creator must point at the other skill"
    assert "import-package" not in CREATOR
    assert "<knowledge_root>" not in CREATOR


def test_consumer_does_not_teach_creation() -> None:
    assert "autocontext-creator" in CONSUMER, "consumer must point at the other skill"
    for command in ("autoctx run ", "autoctx solve ", "autoctx improve ", "autoctx judge "):
        assert command not in CONSUMER, f"{command!r} belongs in the creator skill"


def test_consumer_documents_the_on_disk_layout() -> None:
    """The reporter asked for "file and folder format info" explicitly."""
    for path in ("playbook.md", "lessons.json", "hints.md", "mutation_log.jsonl", "reports/"):
        assert path in CONSUMER, f"{path} missing from the layout section"
    assert "AUTOCONTEXT_KNOWLEDGE_ROOT" in CONSUMER


# --- Every documented command is real ------------------------------------


def _documented_commands(skill: str) -> set[str]:
    """Top-level `autoctx <command>` invocations shown in the skill."""
    return set(re.findall(r"^autoctx ([a-z][a-z-]*)", skill, re.MULTILINE))


# ids= matters here: without it pytest names the case after the parameter
# value, and the parameter is the whole skill. A 4KB test id makes a real
# failure unreadable, which is how a caught bug still gets ignored.
@pytest.mark.parametrize(
    "skill,label",
    [(CREATOR, "creator"), (CONSUMER, "consumer")],
    ids=["creator", "consumer"],
)
def test_every_documented_command_exists(skill: str, label: str) -> None:
    """Checked against the real Typer app, not against a hand-kept list.

    This is the assertion that would have caught the first draft of the
    consumer skill, which showed `autoctx import-package --json` without the
    required PACKAGE_FILE argument.
    """
    from autocontext.cli import app

    real = {command.name for command in app.registered_commands if command.name}
    real |= {group.name for group in app.registered_groups if group.name}
    # Typer derives a name from the function when none is given explicitly.
    real |= {(command.callback.__name__.replace("_", "-") if command.callback else "") for command in app.registered_commands}

    for name in _documented_commands(skill):
        assert name in real, f"{label} skill documents `autoctx {name}`, which is not a command"


def test_the_command_check_can_fail() -> None:
    """A guard that cannot catch anything is decoration."""
    assert _documented_commands("autoctx definitely-not-a-command --json\n") == {"definitely-not-a-command"}


# --- Committed files cannot drift ----------------------------------------


@pytest.mark.parametrize(
    "name,render",
    [
        (AUTOCONTEXT_CREATOR_SKILL_NAME, render_creator_skill),
        (AUTOCONTEXT_CONSUMER_SKILL_NAME, render_consumer_skill),
    ],
)
def test_committed_file_matches_the_renderer(name: str, render) -> None:
    """The Python is the source; the file is a build artifact.

    Mirrors the existing Hermes drift test. Without this the committed copy is
    a hand-maintained duplicate, which is how the two get out of step and the
    published skill starts describing a CLI that moved on.
    """
    committed = SKILLS_DIR / name / "SKILL.md"
    assert committed.exists(), f"missing {committed.relative_to(REPO_ROOT)}"
    assert committed.read_text(encoding="utf-8") == render(), (
        f"committed skills/{name}/SKILL.md drifted from the renderer. Regenerate with:\n"
        f"  uv run autoctx skills export {name} --output skills/{name}/SKILL.md --force"
    )


@pytest.mark.parametrize(
    "name",
    [AUTOCONTEXT_CREATOR_SKILL_NAME, AUTOCONTEXT_CONSUMER_SKILL_NAME],
)
def test_committed_file_is_actually_tracked_by_git(name: str) -> None:
    """Existing on disk is not the same as being committed.

    `skills/` is an ignored runtime-output directory; each committed snapshot
    is un-ignored by name in .gitignore. The drift test above reads the
    filesystem, so a file that was generated but never tracked passes locally
    and fails in CI on a fresh checkout -- which is exactly what happened here.

    Skipped rather than failed when git is unavailable: this asserts a property
    of the repository, and a source tree without git history has no answer.
    """
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git unavailable")
    relative = f"skills/{name}/SKILL.md"
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", relative],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if "not a git repository" in result.stderr.lower():
        pytest.skip("not a git checkout")
    assert result.returncode == 0, (
        f"{relative} exists but is not tracked by git. `skills/` is ignored by "
        "default, so a new snapshot needs an explicit `!skills/<name>/` line in "
        ".gitignore."
    )


def test_the_registry_lists_both_skills() -> None:
    assert set(GENERIC_SKILL_RENDERERS) == {
        AUTOCONTEXT_CREATOR_SKILL_NAME,
        AUTOCONTEXT_CONSUMER_SKILL_NAME,
    }


def test_the_hermes_skill_is_left_alone() -> None:
    """This issue ADDS skills; it does not retire the Hermes one.

    Someone already adapted that skill for their own use, per the report.
    """
    assert (SKILLS_DIR / "autocontext" / "SKILL.md").exists()
