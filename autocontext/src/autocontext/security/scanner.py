"""TruffleHog backstop scanner for artifact secret detection.

Wraps the ``trufflehog`` CLI as a defense-in-depth layer. Any finding
— verified or not — flags the artifact. The scanner degrades gracefully
when trufflehog is not installed: scan returns clean with
``scanner_available=False``.

Pattern follows https://github.com/badlogic/pi-share-hf: deterministic
redaction first, trufflehog as backstop, any finding blocks.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_TRUFFLEHOG_TIMEOUT = 30  # seconds


def is_trufflehog_available() -> bool:
    """Check if trufflehog CLI is on PATH."""
    return shutil.which("trufflehog") is not None


@dataclass(slots=True)
class ScanFinding:
    """A single secret finding from trufflehog."""

    detector: str
    file_path: str
    verified: bool
    raw_preview: str  # first 20 chars of the raw secret for audit logs

    @classmethod
    def from_trufflehog_json(cls, raw: dict[str, Any]) -> ScanFinding:
        """Parse a single trufflehog JSON output line."""
        source = raw.get("SourceMetadata", {}).get("Data", {}).get("Filesystem", {})
        file_path = source.get("file", "")
        detector = raw.get("DetectorName", "unknown")
        verified = raw.get("Verified", False)
        secret_raw = raw.get("Raw", "")
        preview = secret_raw[:20] + "..." if len(secret_raw) > 20 else secret_raw
        return cls(
            detector=detector,
            file_path=file_path,
            verified=verified,
            raw_preview=preview,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector": self.detector,
            "file_path": self.file_path,
            "verified": self.verified,
            "raw_preview": self.raw_preview,
        }


@dataclass(slots=True)
class ScanResult:
    """Outcome of scanning a directory for secrets."""

    findings: list[ScanFinding]
    scanned_path: str
    scanner_available: bool
    scan_error: str | None = None

    @property
    def is_clean(self) -> bool:
        """Clean if no findings were reported and the scan did not fail."""
        return len(self.findings) == 0 and self.scan_error is None

    @property
    def finding_count(self) -> int:
        return len(self.findings)

    @property
    def flagged_files(self) -> set[str]:
        """Set of file paths that had at least one finding."""
        return {f.file_path for f in self.findings}

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_clean": self.is_clean,
            "finding_count": self.finding_count,
            "scanner_available": self.scanner_available,
            "scanned_path": self.scanned_path,
            "scan_error": self.scan_error,
            "findings": [f.to_dict() for f in self.findings],
            "flagged_files": sorted(self.flagged_files),
        }


class SecretScanner:
    """Wraps trufflehog CLI for filesystem secret scanning."""

    def __init__(self, timeout: int = _TRUFFLEHOG_TIMEOUT) -> None:
        self._timeout = timeout
        self._available: bool | None = None

    @property
    def available(self) -> bool:
        if self._available is None:
            self._available = is_trufflehog_available()
        return self._available

    def scan(self, directory: str) -> ScanResult:
        """Scan a directory for secrets. Returns ScanResult.

        Gracefully degrades: if trufflehog is not installed, returns clean
        result with ``scanner_available=False``.
        """
        if not self.available:
            logger.debug("trufflehog not installed — skipping secret scan")
            return ScanResult(findings=[], scanned_path=directory, scanner_available=False)

        try:
            result = subprocess.run(
                [
                    "trufflehog",
                    "filesystem",
                    "--directory",
                    directory,
                    "--json",
                    "--no-update",
                ],
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired:
            error = f"trufflehog scan timed out after {self._timeout}s"
            logger.warning(error)
            return ScanResult(findings=[], scanned_path=directory, scanner_available=True, scan_error=error)
        except OSError as exc:
            logger.warning("trufflehog scan failed: %s", exc)
            return ScanResult(
                findings=[],
                scanned_path=directory,
                scanner_available=False,
                scan_error=str(exc),
            )

        findings: list[ScanFinding] = []
        for line in result.stdout.strip().splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                findings.append(ScanFinding.from_trufflehog_json(raw))
            except (json.JSONDecodeError, KeyError):
                continue

        if findings:
            logger.warning(
                "trufflehog found %d secret(s) in %s — flagging artifacts",
                len(findings),
                directory,
            )
            return ScanResult(findings=findings, scanned_path=directory, scanner_available=True)

        if result.returncode != 0:
            detail = result.stderr.strip().splitlines()[0] if result.stderr.strip() else ""
            error = f"trufflehog exited with code {result.returncode}"
            if detail:
                error = f"{error}: {detail}"
            logger.warning("trufflehog scan failed for %s: %s", directory, error)
            return ScanResult(findings=[], scanned_path=directory, scanner_available=True, scan_error=error)

        return ScanResult(findings=[], scanned_path=directory, scanner_available=True)
