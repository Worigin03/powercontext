# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Chunked context packs and prefix-reusing deltas for incremental transfer."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from typing import Any

PACK_SCHEMA = "powercontext.context-pack.v1"
DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
DEFAULT_CHUNK_BYTES = 512
_DIGEST_PREFIX = "sha256:"
_DIGEST_HEX_LENGTH = 64
_PACK_FIELDS = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_FIELDS = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})


class ContextPackError(Exception):
    """Report one protocol, chunking or integrity violation."""


@dataclass(frozen=True, slots=True)
class ContextPack:
    """A complete context body split into ordered, individually valid chunks."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        """Build the immutable pack and its self-referential wire byte count."""

        normalized = _normalize_items(items)
        digest = _pack_digest(normalized)
        byte_count = 0
        for _ in range(4):
            candidate = _pack_bytes(normalized, digest, byte_count)
            if len(candidate) == byte_count:
                return cls(items=normalized, digest=digest, byte_count=byte_count)
            byte_count = len(candidate)
        return cls(items=normalized, digest=digest, byte_count=byte_count)

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> ContextPack:
        """Split text into deterministic chunks that each stay valid UTF-8."""

        return cls.build(_chunk_content(content, chunk_bytes))

    def to_bytes(self) -> bytes:
        """Serialize exactly the wire fields the receiver contract allows."""

        return _pack_bytes(self.items, self.digest, self.byte_count)

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPack:
        """Decode and verify one complete pack."""

        payload = _decode_object(raw, _PACK_FIELDS)
        if payload["schema"] != PACK_SCHEMA:
            raise ContextPackError("unsupported context pack schema")  # noqa: TRY003
        items = _decode_items(payload["items"])
        digest = _require_digest(payload["digest"], "digest")
        byte_count = _require_count(payload["byte_count"], "byte_count")
        if byte_count != len(raw):
            raise ContextPackError("context pack byte count does not match its wire bytes")  # noqa: TRY003
        if digest != _pack_digest(items):
            raise ContextPackError("context pack digest does not match its chunks")  # noqa: TRY003
        return cls(items=items, digest=digest, byte_count=byte_count)


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """A retained base prefix plus the target chunks that must follow it."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        """Reuse the longest common chunk prefix and append the rest."""

        prefix = 0
        for base_item, target_item in zip(base.items, target.items, strict=False):
            if base_item != target_item:
                break
            prefix += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix,
            appended=target.items[prefix:],
        )

    @property
    def byte_count(self) -> int:
        """Expose the delta's own wire size without adding a wire field."""

        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Serialize exactly the wire fields the receiver contract allows."""

        payload = {
            "schema": DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": _encode_items(self.appended),
        }
        return _canonical_bytes(payload)

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPackDelta:
        """Decode and verify one delta."""

        payload = _decode_object(raw, _DELTA_FIELDS)
        if payload["schema"] != DELTA_SCHEMA:
            raise ContextPackError("unsupported context pack delta schema")  # noqa: TRY003
        prefix_items = _require_prefix(payload["prefix_items"])
        return cls(
            base_digest=_require_digest(payload["base_digest"], "base_digest"),
            target_digest=_require_digest(payload["target_digest"], "target_digest"),
            prefix_items=prefix_items,
            appended=_decode_items(payload["appended"]),
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Rebuild the target pack from one retained base pack and its delta."""

    if not isinstance(base, ContextPack) or not isinstance(delta, ContextPackDelta):
        raise ContextPackError("delta application requires a pack and a delta")  # noqa: TRY003
    if delta.base_digest != base.digest:
        raise ContextPackError("delta was built for a different base pack")  # noqa: TRY003
    if delta.prefix_items > len(base.items):
        raise ContextPackError("delta prefix exceeds the base chunk count")  # noqa: TRY003
    rebuilt = ContextPack.build((*base.items[: delta.prefix_items], *delta.appended))
    if rebuilt.digest != delta.target_digest:
        raise ContextPackError("rebuilt context does not match the delta target digest")  # noqa: TRY003
    return rebuilt


def _chunk_content(content: str, chunk_bytes: int) -> tuple[bytes, ...]:
    if not isinstance(content, str):
        raise ContextPackError("context content must be text")  # noqa: TRY003
    if not isinstance(chunk_bytes, int) or isinstance(chunk_bytes, bool) or chunk_bytes < 1:
        raise ContextPackError("chunk_bytes must be a positive integer")  # noqa: TRY003
    chunks: list[bytes] = []
    # Line-aligned chunks keep appended text from moving earlier boundaries, so a
    # growing context reuses its whole prefix instead of its last fixed window.
    for line in content.splitlines(keepends=True):
        encoded = line.encode("utf-8")
        if len(encoded) <= chunk_bytes:
            chunks.append(encoded)
        else:
            chunks.extend(_split_encoded(encoded, chunk_bytes))
    return tuple(chunks)


def _split_encoded(encoded: bytes, chunk_bytes: int) -> tuple[bytes, ...]:
    chunks: list[bytes] = []
    start = 0
    while start < len(encoded):
        end = min(start + chunk_bytes, len(encoded))
        while end > start and end < len(encoded) and encoded[end] & 0xC0 == 0x80:
            end -= 1
        if end == start:
            raise ContextPackError("chunk_bytes cannot hold one encoded character")  # noqa: TRY003
        chunks.append(encoded[start:end])
        start = end
    return tuple(chunks)


def _normalize_items(items: tuple[bytes, ...]) -> tuple[bytes, ...]:
    normalized = tuple(items)
    for item in normalized:
        if not isinstance(item, bytes):
            raise ContextPackError("context pack chunks must be bytes")  # noqa: TRY003
    return normalized


def _pack_digest(items: tuple[bytes, ...]) -> str:
    canonical = _canonical_bytes({"schema": PACK_SCHEMA, "items": _encode_items(items)})
    return _DIGEST_PREFIX + hashlib.sha256(canonical).hexdigest()


def _pack_bytes(items: tuple[bytes, ...], digest: str, byte_count: int) -> bytes:
    return _canonical_bytes(
        {
            "schema": PACK_SCHEMA,
            "items": _encode_items(items),
            "digest": digest,
            "byte_count": byte_count,
        }
    )


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _encode_items(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _decode_items(value: Any) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError("chunk list must be a JSON array")  # noqa: TRY003
    decoded: list[bytes] = []
    for entry in value:
        if not isinstance(entry, str):
            raise ContextPackError("chunk entries must be base64 strings")  # noqa: TRY003
        try:
            decoded.append(base64.b64decode(entry, validate=True))
        except (binascii.Error, ValueError) as error:
            raise ContextPackError("chunk entry is not valid base64") from error  # noqa: TRY003
    return tuple(decoded)


def _decode_object(raw: bytes, expected: frozenset[str]) -> dict[str, Any]:
    if not isinstance(raw, (bytes, bytearray)):
        raise ContextPackError("wire payload must be bytes")  # noqa: TRY003
    try:
        payload = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ContextPackError("wire payload is not valid UTF-8 JSON") from error  # noqa: TRY003
    if not isinstance(payload, dict):
        raise ContextPackError("wire payload must be a JSON object")  # noqa: TRY003
    keys = set(payload)
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        raise ContextPackError(f"wire payload fields are invalid (missing={missing}, extra={extra})")  # noqa: TRY003
    return payload


def _require_digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.startswith(_DIGEST_PREFIX):
        raise ContextPackError(f"{field} must be a sha256 digest")  # noqa: TRY003
    hexadecimal = value[len(_DIGEST_PREFIX) :]
    if len(hexadecimal) != _DIGEST_HEX_LENGTH:
        raise ContextPackError(f"{field} must be a sha256 digest")  # noqa: TRY003
    try:
        int(hexadecimal, 16)
    except ValueError as error:
        raise ContextPackError(f"{field} must be a sha256 digest") from error  # noqa: TRY003
    if hexadecimal != hexadecimal.lower():
        raise ContextPackError(f"{field} must be a lowercase sha256 digest")  # noqa: TRY003
    return value


def _require_count(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ContextPackError(f"{field} must be a non-negative integer")  # noqa: TRY003
    return value


def _require_prefix(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ContextPackError("prefix_items must be a non-negative integer")  # noqa: TRY003
    return value


__all__ = [
    "DEFAULT_CHUNK_BYTES",
    "DELTA_SCHEMA",
    "PACK_SCHEMA",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
