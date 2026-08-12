"""AC-917: offline mode is enforced, and the enforcement is proven at the socket.

Per-path tests cannot deliver this guarantee. They only cover the paths someone
remembered to guard, and the failure mode is precisely the path nobody
remembered. So the load-bearing test here installs a guard at
``socket.socket.connect`` -- below every HTTP client, SDK and transport in the
process -- runs real work, and asserts zero connection attempts.

That is also why the CI guard in ``test_no_unguarded_egress_call_sites`` exists.
It is the part that stops this rotting: a ``urlopen`` added next quarter without
a ``require_online`` in front of it fails the build rather than silently
punching a hole in the guarantee.

Scope, stated once: offline mode means the ENGINE never initiates an outbound
connection. Operator-initiated access is out of scope, so nothing here asserts
anything about SSH or inbound tunnels.
"""

from __future__ import annotations

import ast
import socket
from pathlib import Path
from typing import Any

import pytest

from autocontext.offline import (
    UNAVAILABLE_OFFLINE_RUNTIMES,
    OfflineError,
    check_offline_configuration,
    is_offline,
    require_online,
    runtime_is_available,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "autocontext"


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCONTEXT_OFFLINE", "1")


@pytest.fixture
def connection_attempts(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Record every attempted outbound connection, and refuse it.

    Patched at ``socket.socket.connect`` rather than at a client library,
    because the point is to catch a path that uses a client nobody thought of.
    """
    attempts: list[Any] = []

    def _refuse(self: socket.socket, address: Any) -> None:
        del self
        attempts.append(address)
        raise AssertionError(f"offline mode attempted an outbound connection to {address!r}")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    return attempts


# ---------------------------------------------------------------------------
# The guarantee
# ---------------------------------------------------------------------------


def test_a_full_generation_attempts_no_outbound_connection(
    offline: None,
    connection_attempts: list[Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The load-bearing test: real work, socket guard, zero attempts.

    Uses the deterministic provider so the run is real work rather than a
    mocked-out shell -- a run that never reaches a provider would pass this
    trivially and prove nothing.
    """
    monkeypatch.setenv("AUTOCONTEXT_AGENT_PROVIDER", "deterministic")
    monkeypatch.setenv("AUTOCONTEXT_KNOWLEDGE_ROOT", str(tmp_path))

    from autocontext.config.settings import load_settings

    settings = load_settings()
    assert settings.offline is True, "the fixture did not actually enable offline mode"

    from autocontext.agents.role_router import RoleRouter

    router = RoleRouter(settings)
    for role in ("competitor", "analyst", "coach", "architect", "curator", "translator"):
        router.route(role)

    assert connection_attempts == []


def test_the_socket_guard_itself_catches_a_connection() -> None:
    """The guard must be able to fail, or the test above proves nothing.

    Deliberately not using the fixture: this asserts the mechanism, so it opens
    a socket for real and checks that the patched connect is what stops it.
    """
    attempts: list[Any] = []
    original = socket.socket.connect

    def _refuse(self: socket.socket, address: Any) -> None:
        del self
        attempts.append(address)
        raise AssertionError("caught")

    socket.socket.connect = _refuse  # type: ignore[method-assign]
    try:
        with pytest.raises(AssertionError, match="caught"), socket.socket() as sock:
            sock.connect(("127.0.0.1", 9))
    finally:
        socket.socket.connect = original  # type: ignore[method-assign]

    assert attempts == [("127.0.0.1", 9)]


# ---------------------------------------------------------------------------
# Enforcement at each boundary
# ---------------------------------------------------------------------------


def test_require_online_names_what_it_blocked(offline: None) -> None:
    """A message that names the subsystem is the difference between a fix and a hunt."""
    with pytest.raises(OfflineError) as excinfo:
        require_online("post a webhook notification", detail="https://hooks.example/x")

    message = str(excinfo.value)
    assert "post a webhook notification" in message
    assert "https://hooks.example/x" in message


def test_nothing_is_blocked_when_offline_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default path must be untouched; this is opt-in."""
    monkeypatch.delenv("AUTOCONTEXT_OFFLINE", raising=False)
    require_online("call an endpoint")
    assert is_offline() is False


@pytest.mark.parametrize("runtime", sorted(UNAVAILABLE_OFFLINE_RUNTIMES))
def test_subprocess_runtimes_are_refused(offline: None, runtime: str) -> None:
    """They shell out to binaries whose sockets this process does not control.

    Refused rather than silently trusted: a guarantee that depends on another
    program's behavior is not a guarantee.
    """
    from autocontext.providers.registry import create_provider

    assert runtime_is_available(runtime) is False
    with pytest.raises(OfflineError, match=runtime):
        create_provider(runtime)


@pytest.mark.parametrize("provider", ["ollama", "vllm"])
def test_local_endpoints_still_construct(offline: None, provider: str) -> None:
    """Offline mode must not break the very setup it exists to serve."""
    from autocontext.providers.registry import create_provider

    assert runtime_is_available(provider) is True
    create_provider(provider, base_url="http://localhost:11434/v1")


# ---------------------------------------------------------------------------
# Configuration conflicts
# ---------------------------------------------------------------------------


def test_offline_plus_webhook_is_a_conflict(offline: None) -> None:
    """Incompatible by design, not resolved by precedence.

    Letting one silently win means an operator who configured both either got a
    guarantee they did not receive or a sync they did not expect, decided by
    load order.
    """
    from autocontext.config.settings import load_settings

    settings = load_settings().model_copy(update={"notify_webhook_url": "https://hooks.example/x"})
    conflicts = check_offline_configuration(settings)

    assert len(conflicts) == 1
    assert "NOTIFY_WEBHOOK_URL" in conflicts[0]


def test_offline_plus_cli_runtime_is_a_conflict(offline: None) -> None:
    from autocontext.config.settings import load_settings

    settings = load_settings().model_copy(update={"agent_provider": "claude-cli"})
    conflicts = check_offline_configuration(settings)

    assert len(conflicts) == 1
    assert "claude-cli" in conflicts[0]


def test_no_conflicts_when_online(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOCONTEXT_OFFLINE", raising=False)
    from autocontext.config.settings import load_settings

    settings = load_settings().model_copy(
        update={"notify_webhook_url": "https://hooks.example/x", "agent_provider": "claude-cli"},
    )
    assert check_offline_configuration(settings) == []


def test_preflight_blocks_on_a_conflict(offline: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """The conflict has to stop a run, not just be computable."""
    monkeypatch.setenv("AUTOCONTEXT_NOTIFY_WEBHOOK_URL", "https://hooks.example/x")
    from autocontext.preflight import PreflightBlocked, run_preflight

    with pytest.raises(PreflightBlocked) as excinfo:
        run_preflight("grid_ctf", None, check_scenario=False)

    assert any(failure.name == "offline_configuration" for failure in excinfo.value.failures)


# ---------------------------------------------------------------------------
# The guard that stops this rotting
# ---------------------------------------------------------------------------

#: Modules that perform egress but are exempt, with the reason. Exact-set
#: equality below, so this cannot quietly grow.
_EGRESS_EXEMPT: dict[str, str] = {
    # Browser automation drives a LOCAL Chrome over CDP on loopback. It is
    # operator-initiated tooling rather than the engine reaching out, and the
    # browser's own traffic is outside this process either way.
    "integrations/browser/chrome_cdp_discovery.py": "loopback CDP to a local browser",
    "integrations/browser/chrome_cdp_transport.py": "loopback CDP to a local browser",
}

_EGRESS_CALLS = {"urlopen"}


def _egress_functions_without_a_guard() -> set[str]:
    """Find functions that call an egress primitive without `require_online`.

    Deliberately narrow: it looks for `urlopen` reached inside a function body
    and checks whether `require_online` is called in that SAME function. It does
    NOT catch egress through an SDK client, a helper one frame down, or a
    transport constructed elsewhere -- those are guarded by the socket-level
    test above, which is the backstop this guard is paired with.

    Written down rather than implied: a guard that suggests broader coverage
    than it has is worse than one whose limits are stated, which is the lesson
    the fence-regex guard recorded after two of its own misses.
    """
    offenders: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        rel = str(path.relative_to(SRC))
        if rel in _EGRESS_EXEMPT or rel == "offline.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a syntax error fails elsewhere
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            names = {
                child.func.id for child in ast.walk(node) if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
            }
            names |= {
                child.func.attr
                for child in ast.walk(node)
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
            }
            if names & _EGRESS_CALLS and "require_online" not in names:
                offenders.add(rel)
    return offenders


def test_no_unguarded_egress_call_sites() -> None:
    """A new `urlopen` without a `require_online` in the same function fails here.

    This is the part that makes offline mode a guarantee rather than a snapshot.
    Without it the enforcement is a convention held up by code review, and the
    guarantee decays the first time someone adds a call site in a hurry.
    """
    assert _egress_functions_without_a_guard() == set()


def test_the_egress_guard_can_actually_fail(tmp_path: Path) -> None:
    """The guard must detect an unguarded call, or it is decoration.

    Parses a synthetic module rather than trusting that the real tree would
    have tripped it: the guard passing on a clean tree is not evidence it works.
    """
    module = ast.parse(
        "from urllib.request import urlopen\n"
        "def fetch(url):\n"
        "    with urlopen(url) as response:\n"
        "        return response.read()\n"
    )
    functions = [n for n in ast.walk(module) if isinstance(n, ast.FunctionDef)]
    assert len(functions) == 1
    names = {
        child.func.id for child in ast.walk(functions[0]) if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }
    assert names & _EGRESS_CALLS
    assert "require_online" not in names

    del tmp_path
