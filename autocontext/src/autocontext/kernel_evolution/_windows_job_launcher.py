"""Trusted gate between Windows Job assignment and benchmark execution."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence

_GO = b"\x01"


def main(argv: Sequence[str] | None = None) -> int:
    target_argv = tuple(sys.argv[1:] if argv is None else argv)
    if not target_argv:
        print("Windows benchmark launcher requires a target command", file=sys.stderr)
        return 125
    if sys.stdin.buffer.read(1) != _GO:
        print("Windows benchmark launcher gate closed before target start", file=sys.stderr)
        return 125
    try:
        target = subprocess.Popen(  # noqa: S603
            target_argv,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as exc:
        print(f"Windows benchmark launcher failed to start target: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 125
    return target.wait()


if __name__ == "__main__":
    raise SystemExit(main())
