# ArtifactStore Dead Code Cleanup — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove dead code from ArtifactStore and fix a versioning bypass bug in `restore_knowledge_snapshot`, reducing surface area and improving correctness.

**Architecture:** Delete 4 unused methods from ArtifactStore (1 no-op, 3 facades with 0 production callers), update/remove their dedicated tests, and fix `restore_knowledge_snapshot` to use `write_playbook()` instead of `write_markdown()` so restored snapshots participate in versioning.

**Tech Stack:** Python 3.11+, pytest, uv

---

## Context

### What We Found

Exploration of `mts/src/mts/storage/artifacts.py` (438 lines) revealed:

| Method | Lines | Production Callers | Test Callers | Verdict |
|--------|-------|--------------------|--------------|---------|
| `_prune_playbook_versions(self, versions_dir)` | 75-77 | 0 | 0 | No-op stub, delete |
| `rollback_playbook(self, scenario_name)` | 79-81 | 0 | ~6 (2 test files) | Dead facade, delete |
| `playbook_version_count(self, scenario_name)` | 83-85 | 0 | ~8 (2 test files) | Dead facade, delete |
| `read_playbook_version(self, scenario_name, version_num)` | 87-89 | 0 | ~4 (2 test files) | Dead facade, delete |

Additionally, `restore_knowledge_snapshot` (line 339-371) writes the playbook using `write_markdown()` instead of `write_playbook()`, bypassing the `VersionedFileStore` and its archiving. This is a correctness bug — if a snapshot is restored mid-run, the previous playbook is silently overwritten without versioning.

### Files Affected

- **Modify:** `mts/src/mts/storage/artifacts.py` — delete 4 methods (~15 lines), fix 1 method (~3 line change)
- **Delete:** `mts/tests/test_playbook_delegation.py` — all 5 tests use deleted facades
- **Modify:** `mts/tests/test_playbook_versioning.py` — remove tests that call deleted facades, keep tests that exercise `write_playbook`/`read_playbook` directly
- **Create:** `mts/tests/test_restore_versioning.py` — new test for the fix

### Current State

- 593 tests passing
- Latest commit: `caddf0e` (Phase 5 monolith deletion)

---

## Task 1: Delete `_prune_playbook_versions` No-Op

**Files:**
- Modify: `mts/src/mts/storage/artifacts.py:75-77`

**Step 1: Verify no callers exist**

Run: `cd /Users/jayscambler/Repositories/MTS && grep -rn "_prune_playbook" mts/src mts/tests`
Expected: Only the definition at `artifacts.py:75`

**Step 2: Delete the method**

Remove lines 75-77 from `artifacts.py`:
```python
    def _prune_playbook_versions(self, versions_dir: Path) -> None:
        """No-op: pruning is now handled internally by VersionedFileStore."""
        pass
```

**Step 3: Run tests**

Run: `cd /Users/jayscambler/Repositories/MTS/mts && uv run pytest --tb=short -q`
Expected: 593 passed

**Step 4: Commit**

```bash
git add mts/src/mts/storage/artifacts.py
git commit -m "refactor: delete _prune_playbook_versions no-op from ArtifactStore"
```

---

## Task 2: Delete Dead Facade Methods

**Files:**
- Modify: `mts/src/mts/storage/artifacts.py:79-89`

**Step 1: Verify no production callers exist**

Run: `cd /Users/jayscambler/Repositories/MTS && grep -rn "rollback_playbook\|playbook_version_count\|read_playbook_version" mts/src`
Expected: Only definitions in `artifacts.py` (lines 79, 83, 87)

**Step 2: Delete the three facade methods**

Remove from `artifacts.py`:
```python
    def rollback_playbook(self, scenario_name: str) -> bool:
        """Restore most recent archived version as current playbook."""
        return self._playbook_store(scenario_name).rollback("playbook.md")

    def playbook_version_count(self, scenario_name: str) -> int:
        """Return number of archived playbook versions."""
        return self._playbook_store(scenario_name).version_count("playbook.md")

    def read_playbook_version(self, scenario_name: str, version_num: int) -> str:
        """Read a specific playbook version by number."""
        return self._playbook_store(scenario_name).read_version("playbook.md", version_num)
```

**Step 3: Run tests (expect failures)**

Run: `cd /Users/jayscambler/Repositories/MTS/mts && uv run pytest --tb=short -q`
Expected: Multiple failures in `test_playbook_delegation.py` and `test_playbook_versioning.py`

**Step 4: Delete `test_playbook_delegation.py` entirely**

All 5 tests in this file use the deleted facades. Delete the file:
`mts/tests/test_playbook_delegation.py`

**Step 5: Update `test_playbook_versioning.py`**

The file has 8 tests. Keep tests that exercise core write/read behavior through `write_playbook()` and `read_playbook()`. Delete tests that call the removed facade methods.

**Tests to delete** (call `playbook_version_count`, `read_playbook_version`, or `rollback_playbook`):
- `test_first_write_no_version` — calls `playbook_version_count`
- `test_second_write_creates_version` — calls `playbook_version_count`, `read_playbook_version`
- `test_version_content_matches_previous` — calls `read_playbook_version`
- `test_pruning_at_max` — calls `playbook_version_count`
- `test_rollback_restores_previous` — calls `rollback_playbook`, `playbook_version_count`
- `test_rollback_empty_returns_false` — calls `rollback_playbook`
- `test_version_count_accurate` — calls `playbook_version_count`
- `test_read_specific_version` — calls `read_playbook_version`

All 8 tests use deleted facades. Delete the entire file:
`mts/tests/test_playbook_versioning.py`

Note: The underlying `VersionedFileStore` is tested in `tests/test_harness/test_harness_versioned_store.py` — rollback, versioning, pruning behavior is still covered at the harness layer.

**Step 6: Run tests**

Run: `cd /Users/jayscambler/Repositories/MTS/mts && uv run pytest --tb=short -q`
Expected: 580 passed (593 - 5 delegation - 8 versioning = 580)

**Step 7: Commit**

```bash
git add mts/src/mts/storage/artifacts.py
git rm mts/tests/test_playbook_delegation.py mts/tests/test_playbook_versioning.py
git commit -m "refactor: remove dead playbook facade methods and their tests

rollback_playbook, playbook_version_count, and read_playbook_version
had zero production callers. Underlying VersionedFileStore behavior
is still tested in test_harness_versioned_store.py."
```

---

## Task 3: Fix `restore_knowledge_snapshot` Versioning Bypass

**Files:**
- Modify: `mts/src/mts/storage/artifacts.py:339-371`
- Create: `mts/tests/test_restore_versioning.py`

**Step 1: Write the failing test**

Create `mts/tests/test_restore_versioning.py`:

```python
"""Tests that restore_knowledge_snapshot uses versioned playbook writes."""
from __future__ import annotations

from pathlib import Path

from mts.storage.artifacts import ArtifactStore


def _make_store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        max_playbook_versions=5,
    )


def test_restore_archives_existing_playbook(tmp_path: Path) -> None:
    """Restoring a snapshot should archive the current playbook via write_playbook."""
    store = _make_store(tmp_path)
    scenario = "grid_ctf"

    # Write initial playbook
    store.write_playbook(scenario, "Current playbook content")

    # Create a fake snapshot
    snapshot_dir = tmp_path / "knowledge" / scenario / "snapshots" / "old_run"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "playbook.md").write_text("Restored playbook content", encoding="utf-8")

    # Restore — this should archive "Current playbook content"
    result = store.restore_knowledge_snapshot(scenario, "old_run")
    assert result is True

    # The restored content should be current
    current = store.read_playbook(scenario)
    assert "Restored playbook content" in current

    # The previous playbook should have been archived
    versions_dir = tmp_path / "knowledge" / scenario / "playbook_versions"
    assert versions_dir.exists(), "Expected versioning to archive the previous playbook"
    versions = list(versions_dir.glob("playbook_v*.md"))
    assert len(versions) == 1, f"Expected 1 archived version, found {len(versions)}"
    archived = versions[0].read_text(encoding="utf-8")
    assert "Current playbook content" in archived
```

**Step 2: Run the test to verify it fails**

Run: `cd /Users/jayscambler/Repositories/MTS/mts && uv run pytest tests/test_restore_versioning.py -v`
Expected: FAIL — `versions_dir.exists()` assertion fails because `write_markdown()` bypasses versioning

**Step 3: Fix `restore_knowledge_snapshot` in `artifacts.py`**

Change lines 347-351 from:
```python
        pb_snapshot = snapshot_dir / "playbook.md"
        if pb_snapshot.exists():
            self.write_markdown(
                self.knowledge_root / scenario_name / "playbook.md",
                pb_snapshot.read_text(encoding="utf-8"),
            )
            restored = True
```

To:
```python
        pb_snapshot = snapshot_dir / "playbook.md"
        if pb_snapshot.exists():
            self.write_playbook(scenario_name, pb_snapshot.read_text(encoding="utf-8"))
            restored = True
```

**Step 4: Run the test to verify it passes**

Run: `cd /Users/jayscambler/Repositories/MTS/mts && uv run pytest tests/test_restore_versioning.py -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `cd /Users/jayscambler/Repositories/MTS/mts && uv run pytest --tb=short -q`
Expected: 581 passed (580 + 1 new)

**Step 6: Commit**

```bash
git add mts/src/mts/storage/artifacts.py mts/tests/test_restore_versioning.py
git commit -m "fix: restore_knowledge_snapshot now uses write_playbook for versioning

Previously used write_markdown() which silently overwrote the current
playbook without archiving. Now calls write_playbook() so the previous
playbook is preserved in version history before restoration."
```

---

## Verification

After all 3 tasks:

```bash
# All tests pass
cd /Users/jayscambler/Repositories/MTS/mts && uv run pytest --tb=short -q
# Expected: 581 passed

# Lint clean
uv run ruff check src tests

# Type check
uv run mypy src

# Verify no stale references
grep -rn "rollback_playbook\|playbook_version_count\|read_playbook_version\|_prune_playbook" mts/src mts/tests
# Expected: empty

# Verify VersionedFileStore still tested at harness layer
uv run pytest tests/test_harness/test_harness_versioned_store.py -v
```

## Summary of Changes

| Change | Lines Removed | Lines Added | Tests Removed | Tests Added |
|--------|--------------|-------------|---------------|-------------|
| Delete `_prune_playbook_versions` | 3 | 0 | 0 | 0 |
| Delete 3 facade methods | 9 | 0 | 13 (2 files) | 0 |
| Fix `restore_knowledge_snapshot` | 3 | 1 | 0 | 1 |
| **Total** | **15** | **1** | **13** | **1** |

Net: -14 lines of production code, -12 tests (facades), +1 test (correctness fix). Final count: 581 tests.
