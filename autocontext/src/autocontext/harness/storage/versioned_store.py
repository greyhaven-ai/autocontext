"""Versioned file store with archive, prune, and rollback."""

from __future__ import annotations

from pathlib import Path


class VersionedFileStore:
    """Manages versioned text files with automatic archiving."""

    def __init__(
        self,
        root: Path,
        max_versions: int = 5,
        versions_dir_name: str = ".versions",
        version_prefix: str = "v",
        version_suffix: str = ".txt",
    ) -> None:
        self._root = root
        self._max_versions = max_versions
        self._versions_dir_name = versions_dir_name
        self._version_prefix = version_prefix
        self._version_suffix = version_suffix

    def _versions_dir(self, name: str) -> Path:
        """Return the versions directory for a given file name."""
        if self._versions_dir_name == ".versions":
            return self._root / ".versions" / name
        return self._root / self._versions_dir_name

    def _version_glob(self) -> str:
        """Return glob pattern for version files."""
        return f"{self._version_prefix}*{self._version_suffix}"

    def _version_path(self, versions_dir: Path, num: int) -> Path:
        """Return path for a specific version number."""
        return versions_dir / f"{self._version_prefix}{num:04d}{self._version_suffix}"

    def write(self, name: str, content: str) -> None:
        """Write content, archiving current version first."""
        path = self._root / name
        versions_dir = self._versions_dir(name)
        if path.exists():
            versions_dir.mkdir(parents=True, exist_ok=True)
            existing = path.read_text(encoding="utf-8")
            existing_versions = sorted(versions_dir.glob(self._version_glob()))
            next_num = len(existing_versions) + 1
            self._version_path(versions_dir, next_num).write_text(existing, encoding="utf-8")
            self._prune(versions_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def read(self, name: str, default: str = "") -> str:
        """Read current version. Returns default if file doesn't exist."""
        path = self._root / name
        return path.read_text(encoding="utf-8") if path.exists() else default

    def rollback(self, name: str) -> bool:
        """Restore most recent archived version. Returns False if no versions."""
        versions_dir = self._versions_dir(name)
        if not versions_dir.exists():
            return False
        versions = sorted(versions_dir.glob(self._version_glob()))
        if not versions:
            return False
        latest = versions[-1]
        path = self._root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(latest.read_text(encoding="utf-8"), encoding="utf-8")
        latest.unlink()
        return True

    def version_count(self, name: str) -> int:
        """Return the number of archived versions."""
        versions_dir = self._versions_dir(name)
        if not versions_dir.exists():
            return 0
        return len(list(versions_dir.glob(self._version_glob())))

    def read_version(self, name: str, version: int) -> str:
        """Read a specific archived version by number. Returns empty string if not found."""
        versions_dir = self._versions_dir(name)
        path = self._version_path(versions_dir, version)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def _prune(self, versions_dir: Path) -> None:
        """Remove oldest versions exceeding max_versions."""
        versions = sorted(versions_dir.glob(self._version_glob()))
        while len(versions) > self._max_versions:
            versions[0].unlink()
            versions.pop(0)
