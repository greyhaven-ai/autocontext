#!/usr/bin/env python3
"""
check_python_no_telemetry.py — Enterprise discipline check.

Greps the autocontext source (production_traces + integrations.openai subtrees)
and the openai SDK dist (if installed) for patterns that would indicate
phone-home / analytics network calls beyond the expected openai API endpoints.

The check is intentionally scoped to the shipped SDK surface
(production_traces/ and integrations/) rather than the full autocontext
application, which legitimately talks to many external services.

Checks:
  1. Telemetry SDK imports (sentry, posthog, mixpanel, etc.)
  2. Outbound HTTP calls (requests.get/post, httpx.get/post, urllib.request.urlopen)
     to a hardcoded non-openai URL *in the SDK subtrees*.
  3. openai installed dist — same scan.

Exits 0 on success; non-zero with diagnostic on failure.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"

# Only audit the shipped SDK subtrees — not the full application.
SDK_SUBTREES = [
    SRC_DIR / "autocontext" / "production_traces",
    SRC_DIR / "autocontext" / "integrations",
]

# Telemetry SDK import patterns
TELEMETRY_IMPORT_PATTERNS: list[re.Pattern] = [
    re.compile(r"import\s+sentry_sdk"),
    re.compile(r"from\s+sentry_sdk"),
    re.compile(r"import\s+posthog"),
    re.compile(r"from\s+posthog"),
    re.compile(r"import\s+mixpanel"),
    re.compile(r"from\s+mixpanel"),
    re.compile(r"import\s+segment"),
    re.compile(r"from\s+segment"),
    re.compile(r"import\s+amplitude"),
    re.compile(r"from\s+amplitude"),
    re.compile(r"import\s+datadog"),
    re.compile(r"from\s+datadog"),
    re.compile(r"import\s+rudder"),
    re.compile(r"from\s+rudder"),
]

# Active network call patterns with a hardcoded external URL
# Only matches actual call sites (requests.get, httpx.post, urlopen, etc.)
# NOT bare string literals or comments.
NETWORK_CALL_RE = re.compile(
    r"""(?:requests\.|httpx\.|urllib\.request\.)(?:get|post|put|delete|patch|head|request|urlopen)\s*\(\s*["'](https?://(?!(?:api\.openai\.com|openai\.com|localhost|127\.0\.0\.1|0\.0\.0\.0))[^"']+)["']""",
    re.IGNORECASE,
)

OFFENSES: list[tuple[Path, str, str]] = []


def scan_file(path: Path) -> None:
    try:
        body = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return

    if len(body) > 5_000_000:  # skip auto-generated megafiles
        return

    for pat in TELEMETRY_IMPORT_PATTERNS:
        if pat.search(body):
            OFFENSES.append((path, "telemetry-import", pat.pattern))
            return

    for match in NETWORK_CALL_RE.finditer(body):
        OFFENSES.append((path, "external-network-call", match.group(1)))
        break


def walk(root: Path, exts: tuple[str, ...]) -> list[Path]:
    result = []
    if not root.exists():
        return result
    for p in root.rglob("*"):
        if p.suffix in exts and p.is_file():
            parts = p.parts
            if any(part in (".venv", "__pycache__", "dist", ".git") for part in parts):
                continue
            result.append(p)
    return result


# Scan only the SDK subtrees
sdk_files: list[Path] = []
for subtree in SDK_SUBTREES:
    sdk_files.extend(walk(subtree, (".py",)))

for f in sdk_files:
    scan_file(f)

# Scan openai installed package if present
openai_files: list[Path] = []
try:
    import importlib.util
    spec = importlib.util.find_spec("openai")
    if spec and spec.origin:
        openai_dir = Path(spec.origin).parent
        openai_files = walk(openai_dir, (".py",))
        for f in openai_files:
            scan_file(f)
except Exception:
    pass

if OFFENSES:
    print("[check_python_no_telemetry] FAIL:")
    for path, kind, detail in OFFENSES:
        print(f"  {kind} :: {path} :: {detail[:120]}")
    print(
        "\nautocontext README states: 'Zero telemetry. Traces go where you put them.' "
        "Review the above patterns before shipping."
    )
    sys.exit(1)

scanned = len(sdk_files) + len(openai_files)
print(
    f"[check_python_no_telemetry] OK — {scanned} files scanned "
    f"({len(sdk_files)} SDK source, {len(openai_files)} openai dist); "
    "no telemetry patterns detected."
)
