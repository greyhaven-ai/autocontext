"""Real Docker/SDK integration using a loopback fixture, never paid inference."""

from __future__ import annotations

import argparse
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from autocontext.execution.executable_skills import ProfileV1, _json
from autocontext.util.json_io import write_json
from benchmarks.skill_reuse.study import HERE, freeze, run


def fixture_answer(value):
    try:
        profile = ProfileV1.model_validate(_json(value))
        if profile.schema_version != 1:
            return '{"abstain":true}'
        return json.dumps({"schema_version": 2, "display_name": profile.name,
                           "status": "enabled" if profile.enabled else "disabled"})
    except ValueError:
        return '{"abstain":true}'


@contextmanager
def fixture_endpoint():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            body = json.dumps({"id": "fixture", "object": "chat.completion", "created": 0, "model": request["model"],
                               "choices": [{"index": 0, "finish_reason": "stop", "message": {
                                   "role": "assistant", "content": fixture_answer(request["messages"][-1]["content"])}}],
                               "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def fixture_models(endpoint):
    reference = {"provider": "openai-compatible", "model": "fixture-reference", "base_url": endpoint,
                 "api_key_env": "STUDY_FIXTURE_API_KEY", "input_cost_per_1k": .002, "output_cost_per_1k": .004}
    return {"evidence_kind": "fixture", "reference": reference,
            "cheaper": {**reference, "model": "fixture-cheaper", "input_cost_per_1k": .001, "output_cost_per_1k": .002}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--split", choices=("development", "heldout"), default="development")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    with fixture_endpoint() as (endpoint, requests):
        models_path = args.output / "models.json"
        write_json(models_path, fixture_models(endpoint))
        root = args.output / "frozen"
        freeze(HERE / "protocol.json", HERE / "fixtures/corpus.json", models_path,
               HERE / "fixtures/learning.json", HERE / "fixtures/skill.py", HERE / "fixtures/playbook.md", root)
        report = run(root, split=args.split)
        # Requests contain only generated fixture data and the public contract.
        write_json(args.output / "fixture-requests.json", requests)
        print(json.dumps({"status": report["status"], "decision": report["decision"], "model_calls": len(requests),
                          "report": str(root / args.split / "REPORT.md")}))
        if report["status"] != "complete":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
