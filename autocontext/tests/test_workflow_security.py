from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"
_DOCKERFILE = _REPO_ROOT / "infra" / "docker" / "Dockerfile"
_COMPOSE_FILE = _REPO_ROOT / "infra" / "docker" / "docker-compose.yml"
_RUNTIME_REQUIREMENTS = _DOCKERFILE.parent / "requirements.lock"
_USES_PATTERN = re.compile(r"^\s*(?:-\s+)?uses:\s+([^@\s]+)@([^\s#]+)")
_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")


def test_third_party_actions_are_pinned_to_full_commit_shas() -> None:
    violations: list[str] = []
    for workflow in sorted(_WORKFLOWS.glob("*.yml")):
        for line_number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), start=1):
            match = _USES_PATTERN.match(line)
            if match is None or match.group(1).startswith("./"):
                continue
            if _COMMIT_PATTERN.fullmatch(match.group(2)) is None:
                violations.append(f"{workflow.name}:{line_number}: {match.group(0).strip()}")

    assert violations == [], "Actions must use immutable commits:\n" + "\n".join(violations)


def test_container_build_uses_immutable_and_hash_verified_inputs() -> None:
    dockerfile = _DOCKERFILE.read_text(encoding="utf-8")

    assert re.search(
        r"^ARG PYTHON_IMAGE=python:3\.11-slim@sha256:[0-9a-f]{64}$",
        dockerfile,
        re.MULTILINE,
    )
    assert dockerfile.count("--require-hashes") == 2
    assert "--no-build-isolation --no-deps" in dockerfile
    assert re.search(r"^USER [1-9][0-9]*:[1-9][0-9]*$", dockerfile, re.MULTILINE)


def test_container_requirement_locks_hash_every_package() -> None:
    for name in ("requirements.lock", "build-requirements.lock"):
        lock_text = (_DOCKERFILE.parent / name).read_text(encoding="utf-8")
        package_starts = [
            match.start()
            for match in re.finditer(r"^[A-Za-z0-9][^\n]*==[^\n]*", lock_text, re.MULTILINE)
        ]

        assert package_starts, f"{name} must contain locked packages"
        boundaries = [*package_starts, len(lock_text)]
        for index, start in enumerate(package_starts):
            entry = lock_text[start : boundaries[index + 1]]
            assert "--hash=sha256:" in entry, f"Unhashed requirement in {name}: {entry!r}"


def test_container_runtime_lock_includes_websocket_transport() -> None:
    lock_text = _RUNTIME_REQUIREMENTS.read_text(encoding="utf-8")

    assert re.search(r"^websockets==[^\s]+", lock_text, re.MULTILINE)


def test_python_publish_build_uses_hashed_backend_constraints() -> None:
    workflow = (_WORKFLOWS / "publish-python.yml").read_text(encoding="utf-8")

    assert "uv build" in workflow
    assert "--build-constraints ../infra/docker/build-requirements.lock" in workflow
    assert "--require-hashes" in workflow


def test_publish_oidc_is_limited_to_artifact_only_jobs() -> None:
    for workflow in sorted(_WORKFLOWS.glob("publish-*.yml")):
        document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        for job_name, job in document["jobs"].items():
            permissions = job.get("permissions", {})
            if permissions.get("id-token") != "write":
                continue

            assert job.get("needs"), f"{workflow.name}:{job_name} must consume a built artifact"
            serialized_steps = "\n".join(str(step) for step in job["steps"])
            assert "actions/checkout" not in serialized_steps
            assert "npm ci" not in serialized_steps
            assert "npm run build" not in serialized_steps
            assert "uv build" not in serialized_steps


def test_live_provider_secrets_are_not_available_to_pull_requests() -> None:
    workflow = (_WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    live_job = workflow.split("  primeintellect-live:\n", maxsplit=1)[1]
    assert live_job.startswith("    if: github.event_name != 'pull_request'\n")


def test_compose_enforces_runtime_hardening_defaults() -> None:
    compose = yaml.safe_load(_COMPOSE_FILE.read_text(encoding="utf-8"))
    persistent_targets = {
        "/workspace/runs",
        "/workspace/knowledge",
        "/workspace/skills",
    }
    for name, service in compose["services"].items():
        assert service["read_only"] is True, name
        assert service["cap_drop"] == ["ALL"], name
        assert service["security_opt"] == ["no-new-privileges:true"], name
        assert service["pids_limit"] == 256, name
        mounts = {
            target: source
            for source, target, *_options in (
                volume.split(":") for volume in service["volumes"]
            )
            if target in persistent_targets
        }
        assert set(mounts) == persistent_targets, name
        assert all(source in compose["volumes"] for source in mounts.values()), name
