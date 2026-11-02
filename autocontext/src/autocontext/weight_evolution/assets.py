"""Bounded, data-only asset acquisition and offline SHA-256 verification.

Source scripts are treated as opaque bytes. Nothing here imports upstream code,
opens tensor archives, instantiates a model, or contacts a provider.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import urllib.request
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import IO, BinaryIO, Literal
from urllib.parse import urlsplit

from autocontext.weight_evolution.models import AssetPin, ReproductionLock

_CHUNK_BYTES = 1024 * 1024
DEFAULT_MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024


class AssetError(ValueError):
    """An asset is missing, unsafe, or inconsistent with its immutable pin."""


@dataclass(frozen=True)
class AssetVerification:
    asset_id: str
    relative_path: str
    required: bool
    status: Literal["verified", "missing", "invalid"]
    detail: str


@dataclass(frozen=True)
class VerificationReport:
    lock_digest: str
    assets: tuple[AssetVerification, ...]
    fidelity_gaps: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """All selected assets verified; this is not model/evaluation readiness."""
        return bool(self.assets) and all(item.status == "verified" for item in self.assets)

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": "asset_verification",
            "lock_digest": self.lock_digest,
            "ok": self.ok,
            "assets": [asdict(item) for item in self.assets],
            "fidelity_gaps": list(self.fidelity_gaps),
            "model_execution_performed": False,
        }


def select_assets(lock: ReproductionLock, asset_ids: Iterable[str] | None = None) -> tuple[AssetPin, ...]:
    """Default to required small assets; optional model/tokenizer files are explicit."""
    if asset_ids is None:
        return tuple(asset for asset in lock.assets if asset.required)
    requested = tuple(asset_ids)
    if not requested or len(set(requested)) != len(requested):
        raise AssetError("select one or more distinct asset IDs")
    available = {asset.asset_id: asset for asset in lock.assets}
    unknown = set(requested) - available.keys()
    if unknown:
        raise AssetError(f"unknown asset IDs: {', '.join(sorted(unknown))}")
    return tuple(available[key] for key in requested)


def _asset_path(root: Path, asset: AssetPin) -> Path:
    """Reject symlinks and non-directory ancestors, including within the root."""
    root = root.resolve()
    current = root
    for component in asset.relative_path.split("/"):
        current = current / component
        if current.is_symlink():
            raise AssetError(f"symlink asset path is not allowed: {asset.relative_path}")
        if current != root / asset.relative_path and current.exists() and not current.is_dir():
            raise AssetError(f"asset ancestor is not a directory: {asset.relative_path}")
    return current


def _hash_stream(stream: BinaryIO, expected_size: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    while True:
        data = stream.read(min(_CHUNK_BYTES, expected_size - total + 1))
        if not data:
            return total, digest.hexdigest()
        total += len(data)
        if total > expected_size:
            raise AssetError("asset exceeds pinned byte size")
        digest.update(data)


def _verified_bytes(path: Path, asset: AssetPin) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise AssetError("asset must be a regular file")
        if metadata.st_size != asset.size_bytes:
            raise AssetError(f"size mismatch: expected {asset.size_bytes}, found {metadata.st_size}")
        size, digest = _hash_stream(stream, asset.size_bytes)
        if size != asset.size_bytes or digest != asset.sha256:
            raise AssetError("SHA-256/size does not match the pinned asset")


def verify_assets(
    lock: ReproductionLock, root: Path, *, asset_ids: Iterable[str] | None = None,
) -> VerificationReport:
    """Verify selected local bytes without network access or source execution."""
    results: list[AssetVerification] = []
    for asset in select_assets(lock, asset_ids):
        status: Literal["verified", "missing", "invalid"] = "verified"
        detail = "size and SHA-256 match"
        try:
            path = _asset_path(root, asset)
            _verified_bytes(path, asset)
            if asset.asset_id == lock.patch.manifest_asset_id:
                _verify_patch_metadata(path, lock)
        except FileNotFoundError:
            status, detail = "missing", "asset is not present"
        except (OSError, ValueError) as exc:
            status, detail = "invalid", str(exc)
        results.append(AssetVerification(asset.asset_id, asset.relative_path, asset.required, status, detail))
    return VerificationReport(lock.digest, tuple(results), lock.fidelity_gaps)


def _verify_patch_metadata(path: Path, lock: ReproductionLock) -> None:
    # The bytes have already been authenticated. Still cross-check duplicate
    # metadata in the lock so a changed tensor identity cannot pass validation.
    payload = json.loads(path.read_bytes())
    if not isinstance(payload, dict):
        raise AssetError("patch manifest must be an object")
    expected = [tensor.to_dict() for tensor in lock.patch.tensors]
    if payload.get("tensors") != expected:
        raise AssetError("patch manifest tensor pins disagree with the reproduction lock")
    if payload.get("changed_tensors") != len(expected) or payload.get("changed_entries_total") != lock.patch.changed_entries:
        raise AssetError("patch manifest totals disagree with the reproduction lock")


class _HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: urllib.request.Request, fp: IO[bytes], code: int, msg: str,
        headers: object, newurl: str,
    ) -> urllib.request.Request | None:
        url = urlsplit(newurl)
        if url.scheme != "https" or url.username or url.password:
            raise AssetError("asset redirect must remain credential-free HTTPS")
        # Hugging Face LFS may redirect to a signed HTTPS CDN URL. No ambient
        # auth headers are supplied; the final bytes must match the fixed hash.
        return super().redirect_request(req, fp, code, msg, headers, newurl)  # type: ignore[arg-type]


def _download(asset: AssetPin, target: Path, *, timeout_seconds: float) -> None:
    opener = urllib.request.build_opener(_HTTPSRedirectHandler())
    request = urllib.request.Request(
        asset.url, headers={"User-Agent": "autocontext-reproduction/1", "Accept-Encoding": "identity"},
    )
    with opener.open(request, timeout=timeout_seconds) as response, target.open("wb") as output:
        digest = hashlib.sha256()
        count = 0
        while True:
            chunk = response.read(min(_CHUNK_BYTES, asset.size_bytes - count + 1))
            if not chunk:
                break
            count += len(chunk)
            if count > asset.size_bytes:
                raise AssetError("download exceeds pinned byte size")
            digest.update(chunk)
            output.write(chunk)
        if count != asset.size_bytes or digest.hexdigest() != asset.sha256:
            raise AssetError("downloaded bytes do not match the pinned SHA-256/size")


def fetch_assets(
    lock: ReproductionLock, root: Path, *, asset_ids: Iterable[str] | None = None,
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES, timeout_seconds: float = 30.0,
) -> VerificationReport:
    """Explicit bounded download; keep existing corrupt files for investigation.

    Downloads publish only after authentication. Existing verified files are
    reused. An optional model weight asset cannot be fetched by the default call.
    """
    if type(max_download_bytes) is not int or max_download_bytes <= 0:
        raise AssetError("download byte budget must be positive")
    if not 0 < timeout_seconds <= 300:
        raise AssetError("timeout must be between zero and 300 seconds")
    selected = select_assets(lock, asset_ids)
    if sum(asset.size_bytes for asset in selected) > max_download_bytes:
        raise AssetError("selected assets exceed the download byte budget")
    root.mkdir(parents=True, exist_ok=True)
    for asset in selected:
        destination = _asset_path(root, asset)
        if destination.exists():
            _verified_bytes(destination, asset)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Recheck after directory creation; never write through a symlink.
        destination = _asset_path(root, asset)
        descriptor, name = tempfile.mkstemp(prefix=".asset-", suffix=".partial", dir=destination.parent)
        os.close(descriptor)
        temporary = Path(name)
        try:
            _download(asset, temporary, timeout_seconds=timeout_seconds)
            _verified_bytes(temporary, asset)
            # Hard-link publication is atomic and refuses a concurrent overwrite.
            try:
                os.link(temporary, destination)
            except FileExistsError:
                _verified_bytes(destination, asset)
        finally:
            temporary.unlink(missing_ok=True)
    return verify_assets(lock, root, asset_ids=[asset.asset_id for asset in selected])
