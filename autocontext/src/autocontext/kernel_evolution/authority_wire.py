"""Bounded, non-pickle framing for isolated accelerator authorities."""

from __future__ import annotations

import json
import socket
import struct
from collections.abc import Callable
from typing import TypeVar

from pydantic import ValidationError

from autocontext.kernel_evolution.authority_protocol import (
    AuthorityRequest,
    AuthorityResponse,
    canonical_authority_digest,
)

WIRE_MAGIC = b"ACAB1\0"
MAX_AUTHORITY_HEADER_BYTES = 32 * 1024
MAX_AUTHORITY_PAYLOAD_BYTES = 512 * 1024 * 1024
_PREFIX = struct.Struct("!6sIQ")
_MessageT = TypeVar("_MessageT", AuthorityRequest, AuthorityResponse)


class AuthorityWireError(ValueError):
    """Raised when an authority frame is malformed, oversized, or truncated."""


def _read_exact(receiver: Callable[[int], bytes], size: int) -> bytes:
    payload = bytearray()
    while len(payload) < size:
        chunk = receiver(size - len(payload))
        if not chunk:
            raise AuthorityWireError("authority frame ended before its declared length")
        payload.extend(chunk)
    return bytes(payload)


def encode_authority_frame(
    message: AuthorityRequest | AuthorityResponse,
    payload: bytes,
    *,
    max_payload_bytes: int = MAX_AUTHORITY_PAYLOAD_BYTES,
) -> bytes:
    """Encode one typed header plus opaque tensor bytes with fixed framing."""

    if max_payload_bytes < 0 or len(payload) > max_payload_bytes:
        raise AuthorityWireError("authority payload exceeded its configured byte limit")
    if message.payload_bytes != len(payload) or message.payload_digest != canonical_authority_digest(payload):
        raise AuthorityWireError("authority payload does not match its typed size and digest")
    header = json.dumps(
        message.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    if len(header) > MAX_AUTHORITY_HEADER_BYTES:
        raise AuthorityWireError("authority header exceeded its fixed byte limit")
    return _PREFIX.pack(WIRE_MAGIC, len(header), len(payload)) + header + payload


def receive_authority_frame(
    connection: socket.socket,
    model: type[_MessageT],
    *,
    max_payload_bytes: int = MAX_AUTHORITY_PAYLOAD_BYTES,
) -> tuple[_MessageT, bytes]:
    """Read and validate one frame without invoking pickle or object hooks."""

    if max_payload_bytes < 0:
        raise ValueError("max_payload_bytes must be non-negative")
    prefix = _read_exact(connection.recv, _PREFIX.size)
    magic, header_size, payload_size = _PREFIX.unpack(prefix)
    if magic != WIRE_MAGIC:
        raise AuthorityWireError("authority frame used an unknown wire protocol")
    if header_size < 2 or header_size > MAX_AUTHORITY_HEADER_BYTES:
        raise AuthorityWireError("authority frame declared an invalid header size")
    if payload_size > max_payload_bytes:
        raise AuthorityWireError("authority frame declared an oversized payload")
    raw_header = _read_exact(connection.recv, header_size)
    payload = _read_exact(connection.recv, payload_size)
    try:
        decoded = json.loads(raw_header.decode("utf-8"), parse_constant=_reject_json_constant)
        message = model.model_validate(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, RecursionError, ValueError) as exc:
        raise AuthorityWireError("authority frame contained an invalid typed JSON header") from exc
    if message.payload_bytes != len(payload) or message.payload_digest != canonical_authority_digest(payload):
        raise AuthorityWireError("authority frame payload failed size or digest validation")
    return message, payload


def send_authority_frame(
    connection: socket.socket,
    message: AuthorityRequest | AuthorityResponse,
    payload: bytes,
    *,
    max_payload_bytes: int = MAX_AUTHORITY_PAYLOAD_BYTES,
) -> None:
    """Send exactly one bounded authority frame."""

    connection.sendall(encode_authority_frame(message, payload, max_payload_bytes=max_payload_bytes))


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


__all__ = [
    "MAX_AUTHORITY_HEADER_BYTES",
    "MAX_AUTHORITY_PAYLOAD_BYTES",
    "WIRE_MAGIC",
    "AuthorityWireError",
    "encode_authority_frame",
    "receive_authority_frame",
    "send_authority_frame",
]
