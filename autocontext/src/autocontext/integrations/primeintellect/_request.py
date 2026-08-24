"""Prime request transport helpers and legacy scenario facades."""

from __future__ import annotations

import base64
import json
import re
import shlex
from typing import Any

from autocontext.execution.remote_execution import RemoteExecutionRequest, RemoteExecutionRequirements
from autocontext.execution.scenario_remote_task import build_builtin_scenario_remote_request
from autocontext.scenarios.base import ExecutionLimits


def build_command(request: RemoteExecutionRequest) -> str:
    parts: list[str] = []
    if request.input_artifacts:
        encoded = [
            {"name": artifact.name, "content": base64.b64encode(artifact.content).decode("ascii")}
            for artifact in request.input_artifacts
        ]
        bootstrap = (
            "import base64,json,pathlib\n"
            f"items=json.loads({json.dumps(json.dumps(encoded))})\n"
            "root=pathlib.Path.cwd().resolve()\n"
            "for item in items:\n"
            " p=(root/item['name']).resolve(); p.relative_to(root); p.parent.mkdir(parents=True,exist_ok=True); "
            "p.write_bytes(base64.b64decode(item['content']))\n"
        )
        parts.append("python - <<'PY'\n" + bootstrap + "PY")
    for name, value in sorted(request.environment.items()):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"invalid remote environment name: {name!r}")
        parts.append(f"export {name}={shlex.quote(value)}")
    parts.append(request.command)
    return "\n".join(parts)


def last_json_object(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        # ``parse_remote_stdout`` treats event envelopes separately from the
        # final result payload. Consumers must make the same distinction or a
        # trailing progress event can invalidate an already-ledgered success.
        if isinstance(parsed, dict) and parsed.get("type") != "event":
            return parsed
    return {}


def fallback_local_response(scenario_name: str, seed: int) -> dict[str, Any]:
    """Return the historical caller-side recovery shape."""

    return {
        "result": {
            "score": 0.0,
            "winner": "incumbent",
            "summary": "primeintellect execution unavailable",
            "replay": [{"event": "remote_unavailable"}],
            "metrics": {"remote_available": 0.0},
            "validation_errors": ["remote execution unavailable"],
        },
        "replay": {
            "scenario": scenario_name,
            "seed": seed,
            "narrative": "Remote execution unavailable; fallback result generated.",
            "timeline": [{"event": "remote_unavailable"}],
        },
    }


def build_eval_command(
    requirements: RemoteExecutionRequirements,
    *,
    scenario_name: str,
    strategy: dict[str, Any],
    seed: int,
) -> str:
    """Build the historical scenario command through the packaged entrypoint."""

    request = build_builtin_scenario_remote_request(
        scenario_name,
        strategy,
        seed,
        ExecutionLimits(),
        image=requirements.image,
        cpu_cores=requirements.resources.cpu_cores,
        disk_gb=requirements.resources.disk_gb,
        memory_gb=requirements.resources.memory_gb,
        accelerator=requirements.resources.accelerator,
        region=requirements.region,
        required_telemetry=requirements.required_telemetry,
    )
    return request.command


__all__ = ["build_command", "build_eval_command", "fallback_local_response", "last_json_object"]
