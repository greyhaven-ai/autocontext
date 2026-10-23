# Maintained Accelerate checkpoint security build

The former Accelerate risk acceptance has been removed. CUDA uses
`accelerate==1.15.0+autocontext.1`, a maintained build bundled in the Python
wheel and sdist. This is a local security patch, not a claim that upstream
Accelerate 1.15 fixed CVE-2026-69112.

The [upstream advisory](https://github.com/advisories/GHSA-4j2p-28q2-5m79)
still lists no fixed release. Upstream 1.15 retains the affected loader;
the [proposed upstream fix](https://github.com/huggingface/accelerate/pull/4214)
was closed without merging.

## Install CUDA/SFT

From a source checkout, run inside `autocontext/`:

```bash
uv sync --locked --extra cuda
uv run --frozen python -m autocontext.accelerate_patch
```

From a published release containing this patch, install the base package,
then its bundled build, then the extra:

```bash
python -m pip install autocontext
python -m autocontext.accelerate_patch --install
python -m pip install 'autocontext[cuda]'
python -m autocontext.accelerate_patch
```

The installer verifies the bundled wheel's SHA-256 and installs only that local
file without dependencies. It uses pip, or uv when pip is absent. The CUDA
extra's exact local-version requirement is then satisfied by this build.
Installing the CUDA extra first cannot silently choose stock Accelerate.
Serving, SFT training and TRL training also verify the installed version and
patched source hashes before model loading. Replacing the build with stock
Accelerate produces an actionable error.

Checkpoint loading requires POSIX descriptor-relative, no-follow file access
(Linux CUDA workers and macOS CPU validation). Other platforms fail explicitly
at the secure checkpoint boundary. The CPU regressions do not certify GPU,
distributed, quantized, or Windows execution.

## Security boundary

The patched `load_checkpoint_in_model` snapshots its selected checkpoint into a
private temporary directory before calling the existing tensor loader. Its
public aliases, `load_checkpoint_and_dispatch`, and the quantization helper's
calls into this loader share the same boundary.

- Indexes and shards are opened through held directory descriptors. Final opens
  are nonblocking, and descriptors must refer to regular files before reading.
- Shard names must be nonempty relative paths without absolute paths, Windows
  drives, backslashes, empty components, or dot traversal. Malformed index maps,
  FIFO/device targets, and directory symlinks are rejected.
- Hugging Face snapshot file links remain supported only when they resolve to a
  regular blob in that same repository cache. The blob is opened through anchored
  cache descriptors; an arbitrary escaping symlink is rejected.
- Copying reads the already-open descriptor, so replacing a source path after
  validation cannot redirect the loader. Tensor loading sees only the private
  snapshot. Copying uses bounded chunks and adds temporary disk use proportional
  to the selected checkpoint.

Existing model, placement, dtype, offload and quantization arguments pass through
unchanged. Valid unsharded/sharded binary and safetensors checkpoints, nested
shards and Hub blob links are covered by real CPU tests. This fixes the named
Accelerate checkpoint boundary; it does not make arbitrary model packages,
remote code, user plugins, or other third-party loaders trustworthy. Continue
using reviewed model sources and isolated training workers.

## Build and maintenance

The readable patch is `third_party/accelerate/checkpoint_security.py`.
`scripts/build_patched_accelerate.py` verifies the official upstream wheel:

```text
accelerate-1.15.0-py3-none-any.whl
sha256:97eacca0b73e45cb867dbf8c5d5d4dc32219544300e0c8992c7334dc2ef33cec
```

It adds the shared boundary and one loader decorator, assigns the local version,
recomputes the wheel RECORD, and writes deterministic ZIP metadata. The original
upstream license and notices remain in the wheel. The bundled manifest records
the upstream hash, patched wheel hash and installed source hashes.

```bash
python scripts/build_patched_accelerate.py --check
```

CI reproduces the wheel from that immutable upstream hash and runs the real
Monty and Accelerate regressions. Changing the upstream base requires reviewing
the loader/caller changes, rebuilding the wheel and manifest, updating the exact
CUDA pin and uv lock, and rerunning these tests. Dependabot cannot replace the
maintained build independently of this review.

Full CI and publishing audits still export every group and extra and use strict
pip-audit. The wrapper verifies the bundled artifact, lock source and exported
hash before querying advisories for the fork's upstream version, since the
advisory service cannot identify the unpublished local version. Every reported
vulnerability, skipped dependency, malformed inventory and audit-tool error is a
failure. There is no expiry extension, advisory ignore, or accepted finding.
