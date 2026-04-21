#!/usr/bin/env python3
"""
Cross-runtime parity fixture driver — Python runtime.

Usage: uv run python scripts/drive_parity_fixture.py <fixture-name>

Reads fixture inputs, runs instrument_client with a mock httpx transport,
captures the emitted trace, normalizes non-deterministic fields, and prints
canonical JSON to stdout.

Exit 0 on success, 1 on error.
"""
from __future__ import annotations

import gc
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

# Add the autocontext src to path
ROOT = Path(__file__).parent.parent
FIXTURES_DIR = (
    ROOT.parent
    / "ts"
    / "tests"
    / "integrations"
    / "openai"
    / "parity"
    / "fixtures"
)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python drive_parity_fixture.py <fixture-name>", file=sys.stderr)
        sys.exit(1)

    fixture_name = sys.argv[1]
    fixture_dir = FIXTURES_DIR / fixture_name
    if not fixture_dir.exists():
        print(f"Fixture not found: {fixture_dir}", file=sys.stderr)
        sys.exit(1)

    request_json = json.loads((fixture_dir / "request.json").read_text())
    identity_json = json.loads((fixture_dir / "identity.json").read_text())
    is_error = (fixture_dir / "error.json").exists()
    is_streaming = request_json.get("stream", False)
    is_responses_api = "input" in request_json or request_json.get("endpoint") == "responses"

    import httpx

    # Build mock transport
    if is_error:
        error_json = json.loads((fixture_dir / "error.json").read_text())

        _err_data = error_json
        def _error_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=_err_data["status"],
                json={"error": {"message": _err_data["message"], "type": "api_error", "code": None}},
            )
        transport = httpx.MockTransport(_error_handler)
    elif is_streaming:
        chunks = json.loads((fixture_dir / "response.json").read_text())

        def _stream_handler(request: httpx.Request) -> httpx.Response:
            lines = ""
            for chunk in chunks:
                lines += f"data: {json.dumps(chunk)}\n\n"
            lines += "data: [DONE]\n\n"
            return httpx.Response(
                status_code=200,
                content=lines.encode("utf-8"),
                headers={"content-type": "text/event-stream"},
            )
        transport = httpx.MockTransport(_stream_handler)
    else:
        response_json = json.loads((fixture_dir / "response.json").read_text())

        def _json_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, json=response_json)
        transport = httpx.MockTransport(_json_handler)

    from openai import OpenAI
    from autocontext.integrations.openai import FileSink, instrument_client, autocontext_session

    with tempfile.TemporaryDirectory() as tmp_dir:
        trace_path = Path(tmp_dir) / "traces.jsonl"
        sink = FileSink(trace_path, batch_size=1, flush_interval_seconds=0)

        # Handle salt for session fixtures
        salt_file = fixture_dir / "install-salt.txt"
        original_dir = os.getcwd()
        if salt_file.exists():
            # Write salt to a .autocontext/install-salt path relative to fixture_dir
            salt_dir = Path(tmp_dir)
            (salt_dir / ".autocontext").mkdir(exist_ok=True)
            (salt_dir / ".autocontext" / "install-salt").write_text(salt_file.read_text().strip())
            os.chdir(salt_dir)

        try:
            inner = OpenAI(api_key="test-key", http_client=httpx.Client(transport=transport), max_retries=0)
            client = instrument_client(inner, sink=sink, app_id="parity-test-app", environment_tag="test")

            def run_request() -> None:
                if is_responses_api:
                    try:
                        client.responses.create(**request_json)
                    except Exception:
                        pass
                elif is_streaming:
                    try:
                        stream = client.chat.completions.create(**request_json)
                        if fixture_name == "chat-streaming-abandoned":
                            # Read first chunk then abandon
                            it = iter(stream)
                            next(it)
                            del stream
                            del it
                            gc.collect()
                        else:
                            for _chunk in stream:
                                pass
                    except Exception:
                        pass
                else:
                    try:
                        client.chat.completions.create(**request_json)
                    except Exception:
                        pass
                sink.flush()
                sink.close()

            if identity_json.get("userId") or identity_json.get("sessionId"):
                with autocontext_session(
                    user_id=identity_json.get("userId"),
                    session_id=identity_json.get("sessionId"),
                ):
                    run_request()
            else:
                run_request()

            # Read the emitted trace
            content = trace_path.read_text().strip()
            if not content:
                print("No trace emitted", file=sys.stderr)
                sys.exit(1)

            raw_trace = json.loads(content.split("\n")[0])
        finally:
            os.chdir(original_dir)

    # Normalize non-deterministic fields
    normalized = normalize_trace(raw_trace, fixture_name)

    # Print canonical JSON
    print(canonical_json(normalized))


def normalize_trace(trace: dict[str, Any], fixture_name: str) -> dict[str, Any]:
    t = dict(trace)
    # Normalize traceId
    t["traceId"] = "PARITY_TRACE_ID_NORMALIZED"
    # Normalize timing
    t["timing"] = {
        "startedAt": "2024-01-01T00:00:00Z",
        "endedAt": "2024-01-01T00:00:01Z",
        "latencyMs": 1000,
    }
    # Normalize SDK name + version (different runtimes have different names)
    if isinstance(t.get("source"), dict) and isinstance(t["source"].get("sdk"), dict):
        t["source"] = dict(t["source"])
        t["source"]["sdk"] = {"name": "autocontext-sdk", "version": "0.0.0"}
    # Normalize message timestamps
    if isinstance(t.get("messages"), list):
        t["messages"] = [
            {**m, "timestamp": "2024-01-01T00:00:00Z"}
            for m in t["messages"]
        ]
    # Normalize error fields (message format, stack, and error-type vary between SDK versions/runtimes)
    if isinstance(t.get("outcome"), dict) and isinstance(t["outcome"].get("error"), dict):
        t["outcome"] = dict(t["outcome"])
        err = dict(t["outcome"]["error"])
        if "stack" in err:
            err["stack"] = "NORMALIZED"
        if "message" in err:
            err["message"] = "NORMALIZED"
        if "type" in err:
            err["type"] = "NORMALIZED"
        t["outcome"]["error"] = err
    return t


def canonical_json(obj: Any) -> str:
    if isinstance(obj, list):
        return "[" + ",".join(canonical_json(v) for v in obj) + "]"
    if isinstance(obj, dict):
        keys = sorted(obj.keys())
        return "{" + ",".join(
            json.dumps(k) + ":" + canonical_json(obj[k]) for k in keys
        ) + "}"
    if obj is None:
        return "null"
    return json.dumps(obj)


if __name__ == "__main__":
    main()
