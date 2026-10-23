"""Confine Accelerate checkpoint reads to regular files in a private snapshot.

Local patch for CVE-2026-69112. Copyright 2026 Grey Haven. Apache-2.0.
"""

import contextlib
import functools
import json
import os
import re
import shutil
import stat
import tempfile
from contextvars import ContextVar
from pathlib import Path, PurePosixPath, PureWindowsPath

PATCH_ID = "autocontext-checkpoint-v1"
_HEX = re.compile(r"[0-9a-f]{40,64}")
_INDEX_LIMIT = 16 * 1024 * 1024
_ACTIVE_CHECKPOINT = ContextVar("accelerate_secure_checkpoint", default=None)


def _parts(name):
    if not isinstance(name, str) or not name or "\x00" in name:
        raise ValueError("checkpoint shard name must be a nonempty string")
    if (
        PurePosixPath(name).is_absolute()
        or PureWindowsPath(name).drive
        or "\\" in name
        or any(part in ("", ".", "..") for part in name.split("/"))
    ):
        raise ValueError("checkpoint shard path must stay within its checkpoint folder")
    return name.split("/")


def _directory_flags():
    if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("secure checkpoint loading requires POSIX descriptor-relative file access")
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _hub_blob(root, root_fd, relative, directory_fd, filename):
    """Allow only HF snapshot file links into that repository's own blobs folder."""
    # Directory links remain forbidden. Hub uses file links, including within subfolders.
    ancestors = [root, *root.parents]
    snapshot = next(
        (p for p in ancestors if p.parent.name == "snapshots" and _HEX.fullmatch(p.name)),
        None,
    )
    if snapshot is None or not snapshot.parent.parent.name.startswith(("models--", "datasets--", "spaces--")):
        raise ValueError("checkpoint file symlinks are only supported for Hugging Face cache blobs")
    link = os.readlink(filename, dir_fd=directory_fd)
    expected_blobs = snapshot.parent.parent / "blobs"
    target = Path(os.path.normpath(os.path.join(root, *relative[:-1], link)))
    if target.parent != expected_blobs or not _HEX.fullmatch(target.name):
        raise ValueError("checkpoint symlink escapes its repository cache blobs")
    # Walk from the held snapshot/subfolder descriptor, never reopen an attacker-swappable path.
    repo_fd = os.dup(root_fd)
    try:
        for _ in range(len(root.relative_to(snapshot).parts) + 2):
            parent_fd = os.open("..", _directory_flags(), dir_fd=repo_fd)
            os.close(repo_fd)
            repo_fd = parent_fd
        blobs_fd = os.open("blobs", _directory_flags(), dir_fd=repo_fd)
        try:
            return os.open(
                target.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0),
                dir_fd=blobs_fd,
            )
        finally:
            os.close(blobs_fd)
    finally:
        os.close(repo_fd)


@contextlib.contextmanager
def _regular_file(root, root_fd, name):
    parts = _parts(name)
    directory_fd = os.dup(root_fd)
    file_fd = None
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, _directory_flags(), dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        try:
            file_fd = os.open(
                parts[-1],
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0),
                dir_fd=directory_fd,
            )
        except OSError:
            # This branch must not turn ordinary open failures into a symlink bypass.
            if not stat.S_ISLNK(os.stat(parts[-1], dir_fd=directory_fd, follow_symlinks=False).st_mode):
                raise
            file_fd = _hub_blob(root, root_fd, parts, directory_fd, parts[-1])
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise ValueError("checkpoint entries must be regular files")
        with os.fdopen(file_fd, "rb", closefd=False) as handle:
            yield handle
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(directory_fd)


def _copy_file(root, root_fd, name, destination):
    target = destination.joinpath(*_parts(name))
    target.parent.mkdir(parents=True, exist_ok=True)
    with _regular_file(root, root_fd, name) as source, target.open("wb") as output:
        shutil.copyfileobj(source, output, length=1024 * 1024)
    return target


@contextlib.contextmanager
def secure_checkpoint(checkpoint):
    """Snapshot the selected index and shards before any tensor loader sees them."""
    checkpoint = Path(checkpoint)
    is_directory = checkpoint.is_dir()
    root = (checkpoint if is_directory else checkpoint.parent).resolve(strict=True)
    root_fd = os.open(root, _directory_flags())
    try:
        index_name = None
        if is_directory:
            names = os.listdir(root_fd)
            if "pytorch_model.bin" in names:
                selected = ["pytorch_model.bin"]
            elif "model.safetensors" in names:
                selected = ["model.safetensors"]
            else:
                indexes = [name for name in names if name.endswith(".index.json")]
                if len(indexes) != 1:
                    raise ValueError("checkpoint folder must contain exactly one checkpoint index")
                index_name = indexes[0]
        elif checkpoint.name.endswith(".json"):
            index_name = checkpoint.name
        else:
            selected = [checkpoint.name]
        with tempfile.TemporaryDirectory(prefix="accelerate-checkpoint-") as temporary:
            destination = Path(temporary)
            if index_name is not None:
                with _regular_file(root, root_fd, index_name) as source:
                    raw = source.read(_INDEX_LIMIT + 1)
                if len(raw) > _INDEX_LIMIT:
                    raise ValueError("checkpoint index exceeds its byte limit")
                index = json.loads(raw)
                if not isinstance(index, dict):
                    raise ValueError("checkpoint index must be an object")
                weights = index.get("weight_map", index)
                if not isinstance(weights, dict) or not weights:
                    raise ValueError("checkpoint weight_map must be a nonempty object")
                # Validate the entire map before opening any shard.
                for name in weights.values():
                    _parts(name)
                selected = sorted(set(weights.values()))
                destination.joinpath(index_name).write_bytes(raw)
            paths = {str(destination.joinpath(*_parts(name))): name for name in selected}
            materialized = set()
            if index_name is None:
                # The original loader needs the unsharded file to select its format.
                for path, name in paths.items():
                    _copy_file(root, root_fd, name, destination)
                    materialized.add(path)
            token = _ACTIVE_CHECKPOINT.set((root, root_fd, destination, paths, materialized))
            try:
                if is_directory:
                    yield str(destination)
                else:
                    yield str(destination / (index_name or selected[0]))
            finally:
                _ACTIVE_CHECKPOINT.reset(token)
    except OSError as exc:
        raise ValueError("unsafe or inaccessible checkpoint file") from exc
    finally:
        os.close(root_fd)


def with_secure_checkpoint(function):
    @functools.wraps(function)
    def guarded(model, checkpoint, *args, **kwargs):
        with secure_checkpoint(checkpoint) as snapshot:
            return function(model, snapshot, *args, **kwargs)

    return guarded


def with_secure_state_dict(function):
    """Copy indexed shards only when the existing rank/device logic requests them."""
    @functools.wraps(function)
    def guarded(checkpoint_file, *args, **kwargs):
        active = _ACTIVE_CHECKPOINT.get()
        if active is not None:
            root, root_fd, destination, paths, materialized = active
            path = str(Path(checkpoint_file))
            if path not in paths:
                raise ValueError("tensor loader requested an unregistered checkpoint shard")
            if path not in materialized:
                _copy_file(root, root_fd, paths[path], destination)
                materialized.add(path)
        return function(checkpoint_file, *args, **kwargs)

    return guarded
