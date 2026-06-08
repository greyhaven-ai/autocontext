"""OnPolicyDistillBackend registry registration (CI-safe: pure platform/find_spec checks,
no MLX import, so this runs everywhere unlike the mlx-gated kernel tests)."""

from __future__ import annotations


def test_opd_backend_registered() -> None:
    from autocontext.training.backends import default_backend_registry

    backend = default_backend_registry().get("opd")
    assert backend is not None
    assert backend.name == "opd"


def test_opd_backend_metadata() -> None:
    from autocontext.training.backends import default_backend_registry

    backend = default_backend_registry().get("opd")
    assert backend is not None
    assert backend.supported_runtime_types() == ["checkpoint"]  # LoRA adapter bundle
    assert "opd" in str(backend.default_checkpoint_dir("grid_ctf"))


def test_opd_in_registry_list() -> None:
    from autocontext.training.backends import default_backend_registry

    assert "opd" in default_backend_registry().list_names()
