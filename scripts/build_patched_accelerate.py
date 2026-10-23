"""Reproducibly build the maintained Accelerate checkpoint patch from a verified wheel."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_VERSION = "1.15.0"
VERSION = "1.15.0+autocontext.1"
UPSTREAM_SHA256 = "97eacca0b73e45cb867dbf8c5d5d4dc32219544300e0c8992c7334dc2ef33cec"
DESTINATION = ROOT / "autocontext/src/autocontext/_vendor/accelerate"


def build(upstream: bytes) -> tuple[bytes, dict]:
    if hashlib.sha256(upstream).hexdigest() != UPSTREAM_SHA256:
        raise ValueError("upstream Accelerate wheel hash mismatch")
    old_info = f"accelerate-{UPSTREAM_VERSION}.dist-info"
    info = f"accelerate-{VERSION}.dist-info"
    with zipfile.ZipFile(io.BytesIO(upstream)) as source:
        files = {name.replace(old_info, info): source.read(name) for name in source.namelist()}
    files.pop(f"{info}/RECORD")
    modeling_path = "accelerate/utils/modeling.py"
    modeling = files[modeling_path].decode()
    needle = "def load_checkpoint_in_model("
    if modeling.count(needle) != 1:
        raise ValueError("upstream checkpoint entry point changed")
    modeling = modeling.replace(needle, "@with_secure_checkpoint\n" + needle)
    modeling = modeling.replace(
        "import contextlib\n", "import contextlib\nfrom .._checkpoint_security import with_secure_checkpoint\n", 1,
    )
    files[modeling_path] = modeling.encode()
    files["accelerate/_checkpoint_security.py"] = (ROOT / "third_party/accelerate/checkpoint_security.py").read_bytes()
    init = files["accelerate/__init__.py"].decode()
    if f'__version__ = "{UPSTREAM_VERSION}"' not in init:
        raise ValueError("upstream version declaration changed")
    files["accelerate/__init__.py"] = init.replace(
        f'__version__ = "{UPSTREAM_VERSION}"', f'__version__ = "{VERSION}"',
    ).encode()
    files[f"{info}/METADATA"] = files[f"{info}/METADATA"].replace(
        f"Version: {UPSTREAM_VERSION}\n".encode(), f"Version: {VERSION}\n".encode(), 1,
    )
    records = io.StringIO()
    writer = csv.writer(records, lineterminator="\n")
    for name, content in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        writer.writerow([name, f"sha256={digest}", len(content)])
    writer.writerow([f"{info}/RECORD", "", ""])
    files[f"{info}/RECORD"] = records.getvalue().encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as wheel:
        for name, content in sorted(files.items()):
            entry = zipfile.ZipInfo(name, date_time=(2026, 10, 23, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            wheel.writestr(entry, content, compresslevel=9)
    data = output.getvalue()
    manifest = {
        "package": "accelerate", "version": VERSION, "upstream_version": UPSTREAM_VERSION,
        "upstream_sha256": UPSTREAM_SHA256,
        "advisory": "CVE-2026-69112",
        "wheel": f"accelerate-{VERSION}-py3-none-any.whl",
        "wheel_sha256": hashlib.sha256(data).hexdigest(),
        "patched_files": {name: hashlib.sha256(files[name]).hexdigest() for name in (
            modeling_path, "accelerate/_checkpoint_security.py", "accelerate/__init__.py",
        )},
    }
    return data, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-wheel", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.upstream_wheel:
        upstream = args.upstream_wheel.read_bytes()
    else:
        with urllib.request.urlopen(f"https://pypi.org/pypi/accelerate/{UPSTREAM_VERSION}/json", timeout=30) as response:
            release = json.load(response)
        url = next(f["url"] for f in release["urls"] if f["filename"].endswith(".whl"))
        with urllib.request.urlopen(url, timeout=60) as response:
            upstream = response.read()
    wheel, manifest = build(upstream)
    if args.check:
        if (DESTINATION / manifest["wheel"]).read_bytes() != wheel:
            raise ValueError("patched wheel does not reproduce")
        if json.loads((DESTINATION / "manifest.json").read_text()) != manifest:
            raise ValueError("patched wheel manifest does not reproduce")
    else:
        DESTINATION.mkdir(parents=True, exist_ok=True)
        (DESTINATION / manifest["wheel"]).write_bytes(wheel)
        (DESTINATION / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Verified patched Accelerate {VERSION}: {manifest['wheel_sha256']}")


if __name__ == "__main__":
    main()
