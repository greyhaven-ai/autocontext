"""Strict parent-side decoding for the isolated child JSON protocol."""

from __future__ import annotations

import json
import math
import os
from typing import Any


def decode_isolated_response(raw: bytes) -> Any:
    def reject_nonstandard_constant(value: str) -> Any:
        raise ValueError(f"non-finite JSON constant {value!r} is not allowed")

    def parse_finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("non-finite JSON number is not allowed")
        return parsed

    try:
        return json.loads(
            raw,
            parse_constant=reject_nonstandard_constant,
            parse_float=parse_finite_float,
        )
    except RecursionError as exc:
        raise ValueError("isolated child returned excessively nested JSON") from exc


def write_all(fd: int, payload: bytes) -> None:
    """Write a complete child response despite short writes."""
    view = memoryview(payload)
    while view:
        view = view[os.write(fd, view) :]


def describe_wait_status(status: int) -> str:
    if os.WIFSIGNALED(status):
        return f"signal {os.WTERMSIG(status)}"
    if os.WIFEXITED(status):
        return f"exit {os.WEXITSTATUS(status)}"
    return "unknown status"
