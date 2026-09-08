# Accelerate checkpoint advisory: scoped risk acceptance

Reviewed September 8, 2026. Expires October 8, 2026 at 00:00 UTC.
Scope: CVE-2026-69112 / GHSA-4j2p-28q2-5m79 in Accelerate 1.14.0, only
with the exact CUDA dependency graph recorded in `scripts/python_audit_exception.json`.
The repository owner authorized proceeding with a narrowly documented risk
decision or mitigation after reviewing the upstream dispute. This records the
selected risk acceptance; it is not a claim that Accelerate was patched.

## Upstream status

The [GitHub advisory](https://github.com/advisories/GHSA-4j2p-28q2-5m79)
remains active and lists no patched version. It describes checkpoint index
entries escaping the model directory or naming special files that hang loading.
On September 8, an upstream maintainer
[declined the proposed fix](https://github.com/huggingface/accelerate/pull/4214#issuecomment-5586211932).
Their [security policy](https://github.com/huggingface/accelerate/blob/main/SECURITY.md)
treats loading untrusted model artifacts as an operator trust decision.
We retain the finding in audit output rather than treating that position as
proof that the behavior is harmless.

## Reviewed harness exposure

- Accelerate is in the optional CUDA extra, not the default Python installation,
  the `all` extra, the Docker runtime requirements, or either npm package.
- Native PyTorch training and the CUDA advisor do not use Accelerate.
- SFT serving, SFT training and TRL training load models through Transformers
  and PEFT. We inspected all Python source in the locked Transformers 5.12.1,
  PEFT 0.19.1, TRL 1.6.0 and Accelerate 1.14.0 wheels, verifying each archive
  against its lockfile SHA-256 before reading it; no package code was executed.
- Harness source and the three caller packages contain no references to
  `load_checkpoint_in_model`, `load_checkpoint_and_dispatch` or
  `load_and_quantize_model`. The last helper is Accelerate's additional internal
  route into the affected loader. Its public dispatch helpers used by PEFT and
  Transformers are distinct from checkpoint loading.

This is static exposure analysis of these exact sources, not a formal
non-reachability proof or a GPU integration test. Dynamically loaded extensions,
user scripts directly invoking Accelerate, arbitrary installed dependency
versions and untrusted artifact workflows are outside the acceptance.

## Controls and limitations

1. The CUDA extra pins all four reviewed packages. Changes to their locked
   versions or wheel hashes require a new review; the audit fails until then.
2. A source tripwire blocks harness references to the three checkpoint APIs.
   It is not a sandbox and cannot protect arbitrary user plugins.
3. CI and Python publishing still export and audit every group and extra using
   strict pip-audit. The wrapper reports this exact accepted finding visibly.
   Other packages, versions, CVEs, skipped dependencies, malformed audit results
   and audit-tool errors remain failures. A published fix reported by pip-audit
   also makes this exception fail, requiring an upgrade/review.
4. The exception expires automatically. It must be removed or explicitly
   re-reviewed; extending it is a security-policy change.
5. Operators must use reviewed model/checkpoint sources, prefer safetensors and
   immutable reviewed model revisions, and avoid mounting credentials or unrelated
   sensitive files into training workers. These are operating requirements, not
   newly implemented enforcement in the model-loading API. Do not use this
   acceptance to approve attacker-supplied models or checkpoint directories.

No CUDA backend is removed. No approval environment, fork-workflow policy,
permissions, release-source check or other vulnerability threshold changes.
The exception does not modify already published distributions or certify
arbitrary end-user environments as safe.

## Re-review triggers

Expiry, a dependency fingerprint change, new checkpoint API use, a newly
reported fixed version, or a deployment that accepts untrusted model artifacts
requires reassessment. For the latter, isolate and validate checkpoint loading
or maintain a reviewed upstream patch before enabling that workflow.
