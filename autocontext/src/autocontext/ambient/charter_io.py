"""load and save the ambient charter yaml with actionable errors."""

from __future__ import annotations

from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from autocontext.ambient.charter import Charter


class CharterLoadError(Exception):
    def __init__(self, path: Path, message: str) -> None:
        self.path = path
        super().__init__(f"charter at {path}: {message}")


def load_charter(path: Path) -> Charter:
    if not path.exists():
        raise CharterLoadError(path, "file not found (run `autoctx ambient init` to create one)")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CharterLoadError(path, f"yaml parse error: {exc}") from exc
    if not isinstance(raw, dict):
        raise CharterLoadError(path, "yaml parse error: top level must be a mapping")
    try:
        return Charter(**raw)
    except ValidationError as exc:
        raise CharterLoadError(path, f"schema violation: {exc}") from exc


def save_charter(charter: Charter, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = charter.model_dump(mode="json")
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
