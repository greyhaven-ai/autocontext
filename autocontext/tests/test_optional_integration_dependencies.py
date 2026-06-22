from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PACKAGE_ROOT / "src"


def _run_python_with_blocked_imports(blocked: list[str], script: str) -> None:
    blocker = "\n".join(
        [
            "import importlib.abc",
            "import sys",
            f"BLOCKED = {blocked!r}",
            "class _Blocker(importlib.abc.MetaPathFinder):",
            "    def find_spec(self, fullname, path=None, target=None):",
            "        if any(fullname == name or fullname.startswith(name + '.') for name in BLOCKED):",
            "            raise ModuleNotFoundError(f'No module named {fullname!r}', name=fullname)",
            "        return None",
            "for name in list(sys.modules):",
            "    if any(name == blocked or name.startswith(blocked + '.') for blocked in BLOCKED):",
            "        sys.modules.pop(name, None)",
            "sys.meta_path.insert(0, _Blocker())",
        ]
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{SRC_ROOT}{os.pathsep}{env.get('PYTHONPATH', '')}".rstrip(os.pathsep)
    result = subprocess.run(
        [sys.executable, "-c", blocker + "\n" + script],
        cwd=PACKAGE_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_core_runner_import_does_not_require_primeintellect_sdk() -> None:
    _run_python_with_blocked_imports(
        ["prime_sandboxes"],
        """
import importlib
for module_name in [
    'autocontext',
    'autocontext.execution.executors',
    'autocontext.loop.generation_runner',
    'autocontext.integrations.primeintellect',
]:
    importlib.import_module(module_name)
""",
    )


def test_browser_facade_import_does_not_require_cdp_dependencies() -> None:
    _run_python_with_blocked_imports(
        ["httpx", "websockets"],
        """
import importlib
importlib.import_module('autocontext.integrations.browser')
""",
    )


def test_primeintellect_feature_reports_missing_extra() -> None:
    _run_python_with_blocked_imports(
        ["prime_sandboxes"],
        """
from autocontext.integrations.primeintellect.client import PrimeIntellectClient

result = PrimeIntellectClient(api_key='test-key').warm_provision('smoke')
assert result['status'] == 'failed'
assert 'autocontext[primeintellect]' in result['error']
""",
    )


def test_primeintellect_execute_strategy_preserves_missing_extra_guidance() -> None:
    _run_python_with_blocked_imports(
        ["prime_sandboxes"],
        """
from autocontext.integrations.primeintellect.client import PrimeIntellectClient

try:
    PrimeIntellectClient(api_key='test-key').execute_strategy(
        scenario_name='grid_ctf',
        strategy={'aggression': 0.6, 'defense': 0.4, 'path_bias': 0.5},
        seed=123,
        timeout_seconds=10.0,
        max_memory_mb=512,
        network_access=False,
        max_retries=0,
    )
except RuntimeError as exc:
    assert 'autocontext[primeintellect]' in str(exc)
else:
    raise AssertionError('missing prime-sandboxes was swallowed by fallback')
""",
    )


def test_browser_feature_reports_missing_extra() -> None:
    _run_python_with_blocked_imports(
        ["httpx", "websockets"],
        """
import asyncio
from autocontext.integrations.browser.chrome_cdp_discovery import ChromeCdpDiscoveryError, ChromeCdpTargetDiscovery
from autocontext.integrations.browser.chrome_cdp_transport import ChromeCdpTransportError, ChromeCdpWebSocketTransport

async def main():
    try:
        await ChromeCdpTargetDiscovery('http://127.0.0.1:9222').list_targets()
    except ChromeCdpDiscoveryError as exc:
        assert 'autocontext[browser]' in str(exc)
    else:
        raise AssertionError('missing httpx did not fail with install guidance')

    try:
        await ChromeCdpWebSocketTransport('ws://127.0.0.1:9222/devtools/page/1').connect()
    except ChromeCdpTransportError as exc:
        assert 'autocontext[browser]' in str(exc)
    else:
        raise AssertionError('missing websockets did not fail with install guidance')

asyncio.run(main())
""",
    )
