#!/usr/bin/env python3
"""Cross-runtime parity helper: invoke Python ``hash_user_id``.

Reads ``{"userId": "...", "salt": "..."}`` JSON from stdin, writes the
64-char lowercase hex digest to stdout. Used by P-hashing-parity.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_SRC = os.path.join(HERE, "..", "..", "..", "autocontext", "src")
if os.path.isdir(PY_SRC):
    sys.path.insert(0, PY_SRC)

from autocontext.production_traces.hashing import hash_user_id, hash_session_id  # noqa: E402


def main() -> None:
    raw = sys.stdin.read()
    payload = json.loads(raw)
    mode = payload.get("mode", "user")
    value = payload["value"]
    salt = payload["salt"]
    if mode == "session":
        sys.stdout.write(hash_session_id(value, salt))
    else:
        sys.stdout.write(hash_user_id(value, salt))


if __name__ == "__main__":
    main()
