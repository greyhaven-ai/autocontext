"""Tests for autocontext.harness.identity.store — IdentityStore and DEFAULT_SOULS."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from autocontext.harness.identity.store import DEFAULT_SOULS, IdentityStore
from autocontext.harness.identity.types import AgentIdentity, IdentityTrait, SoulDocument


def _make_identity(role: str = "competitor", *, soul: SoulDocument | None = None) -> AgentIdentity:
    return AgentIdentity(
        role=role,
        soul=soul,
        traits=(
            IdentityTrait(name="advance_rate", value=0.750, trend=0.050, observations=10),
        ),
        trust_tier="proven",
        total_generations=20,
        total_advances=15,
        created_at="2025-01-01T00:00:00+00:00",
        last_updated="2025-01-02T00:00:00+00:00",
        history=(),
    )


# ── Round-trip persistence ──────────────────────────────────────────────────


def test_save_load_round_trip(tmp_path: Path) -> None:
    store = IdentityStore(tmp_path / "identities")
    original = _make_identity(soul=DEFAULT_SOULS["competitor"])

    store.save(original)
    loaded = store.load("competitor")

    assert loaded is not None
    assert loaded.role == original.role
    assert loaded.soul is not None
    assert loaded.soul.purpose == original.soul.purpose  # type: ignore[union-attr]
    assert loaded.traits == original.traits
    assert loaded.trust_tier == original.trust_tier
    assert loaded.total_generations == original.total_generations
    assert loaded.total_advances == original.total_advances
    assert loaded.created_at == original.created_at
    assert loaded.last_updated == original.last_updated
    assert loaded.history == original.history

    # Verify the JSON file is valid and readable
    path = tmp_path / "identities" / "competitor_identity.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["role"] == "competitor"


# ── Loading non-existent role ───────────────────────────────────────────────


def test_load_nonexistent_returns_none(tmp_path: Path) -> None:
    store = IdentityStore(tmp_path / "identities")
    assert store.load("nonexistent") is None


# ── load_or_create — new role ───────────────────────────────────────────────


def test_load_or_create_new_role(tmp_path: Path) -> None:
    store = IdentityStore(tmp_path / "identities")
    identity = store.load_or_create("analyst")

    assert identity.role == "analyst"
    assert identity.soul is not None
    assert identity.soul.purpose == DEFAULT_SOULS["analyst"].purpose
    assert identity.traits == ()
    assert identity.trust_tier == "probation"
    assert identity.total_generations == 0
    assert identity.total_advances == 0
    assert identity.history == ()

    # Should have been persisted
    assert store.exists("analyst")
    reloaded = store.load("analyst")
    assert reloaded is not None
    assert reloaded.role == "analyst"
    assert reloaded.created_at == identity.created_at


# ── load_or_create — existing role ──────────────────────────────────────────


def test_load_or_create_existing_role(tmp_path: Path) -> None:
    store = IdentityStore(tmp_path / "identities")
    original = _make_identity(soul=DEFAULT_SOULS["competitor"])
    store.save(original)

    loaded = store.load_or_create("competitor")

    assert loaded.trust_tier == "proven"  # preserved, not reset to "probation"
    assert loaded.total_generations == 20
    assert loaded.total_advances == 15


# ── load_all ────────────────────────────────────────────────────────────────


def test_load_all(tmp_path: Path) -> None:
    store = IdentityStore(tmp_path / "identities")
    store.save(_make_identity("competitor"))
    store.save(_make_identity("analyst"))
    store.save(_make_identity("coach"))

    all_ids = store.load_all()

    assert set(all_ids.keys()) == {"competitor", "analyst", "coach"}
    for role, identity in all_ids.items():
        assert identity.role == role


def test_load_all_empty_dir(tmp_path: Path) -> None:
    store = IdentityStore(tmp_path / "nonexistent")
    assert store.load_all() == {}


# ── exists ──────────────────────────────────────────────────────────────────


def test_exists_true_and_false(tmp_path: Path) -> None:
    store = IdentityStore(tmp_path / "identities")
    assert store.exists("competitor") is False

    store.save(_make_identity("competitor"))
    assert store.exists("competitor") is True


# ── DEFAULT_SOULS for known roles ──────────────────────────────────────────


def test_default_souls_for_known_roles() -> None:
    expected_roles = {"competitor", "analyst", "coach", "architect"}
    assert set(DEFAULT_SOULS.keys()) == expected_roles

    for role, soul in DEFAULT_SOULS.items():
        assert soul.role == role
        assert len(soul.purpose) > 0
        assert len(soul.principles) >= 2
        assert len(soul.constraints) >= 1


# ── Unknown role gets no soul ──────────────────────────────────────────────


def test_unknown_role_no_soul(tmp_path: Path) -> None:
    store = IdentityStore(tmp_path / "identities")
    identity = store.load_or_create("custom_role_xyz")

    assert identity.role == "custom_role_xyz"
    assert identity.soul is None
    assert identity.trust_tier == "probation"
    assert identity.total_generations == 0


# ── Thread safety ──────────────────────────────────────────────────────────


def test_thread_safety(tmp_path: Path) -> None:
    store = IdentityStore(tmp_path / "identities")
    errors: list[Exception] = []

    def save_role(i: int) -> None:
        try:
            identity = _make_identity(f"role_{i}")
            store.save(identity)
        except Exception as exc:
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(save_role, range(20)))

    assert errors == [], f"Concurrent save raised exceptions: {errors}"

    all_ids = store.load_all()
    assert len(all_ids) == 20
    for i in range(20):
        assert f"role_{i}" in all_ids


# ── Directory creation ─────────────────────────────────────────────────────


def test_creates_directory(tmp_path: Path) -> None:
    deep_dir = tmp_path / "a" / "b" / "c" / "identities"
    assert not deep_dir.exists()

    store = IdentityStore(deep_dir)
    store.save(_make_identity("competitor"))

    assert deep_dir.exists()
    assert (deep_dir / "competitor_identity.json").exists()
