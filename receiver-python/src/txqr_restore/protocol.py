"""Strict TXQRT1 parsing. Hashes detect corruption; they do not authenticate senders."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass

PREFIX = "TXQRT1:"
MAX_FRAME = 16384
MAX_SIZE = 64 * 1024 * 1024
MAX_CHUNKS = 100000
MAX_CHUNK_SIZE = 4096
HEX = re.compile(r"[0-9a-f]{64}\Z")
B64 = re.compile(r"[A-Za-z0-9_-]*\Z")


class ProtocolError(ValueError):
    """An untrusted frame does not conform to the wire protocol."""


@dataclass(frozen=True)
class Limits:
    max_raw_size: int = MAX_SIZE
    max_wire_size: int = MAX_SIZE
    max_chunks: int = MAX_CHUNKS
    max_chunk_size: int = MAX_CHUNK_SIZE

    def __post_init__(self):
        for name, ceiling in (("max_raw_size", MAX_SIZE), ("max_wire_size", MAX_SIZE),
                              ("max_chunks", MAX_CHUNKS), ("max_chunk_size", MAX_CHUNK_SIZE)):
            value = getattr(self, name)
            if type(value) is not int or not (0 if "size" in name and "chunk" not in name else 1) <= value <= ceiling:
                raise ValueError(f"{name} must not exceed protocol limit {ceiling}")


def canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64decode(value: str) -> bytes:
    if type(value) is not str or not B64.fullmatch(value):
        raise ProtocolError("invalid base64url alphabet or padding")
    try:
        result = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ProtocolError("invalid base64url") from exc
    if b64encode(result) != value:
        raise ProtocolError("noncanonical base64url")
    return result


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate JSON key")
        result[key] = value
    return result


def strict_json(data: bytes):
    try:
        value = json.loads(data.decode("ascii"), object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ProtocolError("nonfinite JSON")))
        if canonical_json(value) != data:
            raise ProtocolError("noncanonical JSON")
        return value
    except (UnicodeError, ValueError, TypeError, RecursionError, OverflowError) as exc:
        raise ProtocolError(str(exc)) from exc


def _integer(value, low: int, high: int, field: str):
    if type(value) is not int or not low <= value <= high:
        raise ProtocolError(f"invalid {field}")


def _hash(value, field: str):
    if type(value) is not str or not HEX.fullmatch(value):
        raise ProtocolError(f"invalid {field}")


def validate_metadata(m, limits: Limits = Limits()) -> dict:
    fields = {"v", "name", "raw_size", "raw_sha256", "encoding", "wire_size", "wire_sha256", "chunk_size", "chunks"}
    if type(m) is not dict or set(m) != fields:
        raise ProtocolError("invalid metadata fields")
    _integer(m["v"], 1, 1, "version")
    name = m["name"]
    if (type(name) is not str or not 1 <= len(name) <= 128 or name in (".", "..")
            or any(ord(c) < 32 or ord(c) > 126 or c in "/\\" for c in name)):
        raise ProtocolError("unsafe filename")
    device_stem = name.split(".", 1)[0].rstrip(" ").upper()
    if (":" in name or name.endswith((".", " ")) or device_stem in {"CON", "PRN", "AUX", "NUL"}
            or re.fullmatch(r"(?:COM|LPT)[1-9]", device_stem)):
        raise ProtocolError("filename is not a portable safe basename")
    _integer(m["raw_size"], 0, limits.max_raw_size, "raw_size")
    _integer(m["wire_size"], 0, limits.max_wire_size, "wire_size")
    _hash(m["raw_sha256"], "raw_sha256")
    _hash(m["wire_sha256"], "wire_sha256")
    if m["encoding"] not in ("none", "gzip"):
        raise ProtocolError("invalid encoding")
    _integer(m["chunk_size"], 1, limits.max_chunk_size, "chunk_size")
    _integer(m["chunks"], 1, limits.max_chunks, "chunks")
    if m["chunks"] != max(1, (m["wire_size"] + m["chunk_size"] - 1) // m["chunk_size"]):
        raise ProtocolError("chunk count disagrees with wire size")
    if m["encoding"] == "none" and (m["raw_size"] != m["wire_size"] or m["raw_sha256"] != m["wire_sha256"]):
        raise ProtocolError("unencoded metadata differs")
    return m


@dataclass(frozen=True)
class Frame:
    metadata: dict
    session: str
    index: int
    data: bytes
    chunk_hash: str


def parse_frame(payload: str | bytes, limits: Limits = Limits()) -> Frame:
    if isinstance(payload, bytes):
        if len(payload) > MAX_FRAME:
            raise ProtocolError("frame exceeds maximum size")
        try:
            payload = payload.decode("ascii")
        except UnicodeError as exc:
            raise ProtocolError("frame is not ASCII") from exc
    if type(payload) is not str or len(payload) > MAX_FRAME or not payload.isascii() or not payload.startswith(PREFIX):
        raise ProtocolError("unsupported frame format/version, encoding, or size")
    obj = strict_json(b64decode(payload[len(PREFIX):]))
    if type(obj) is not dict or set(obj) != {"d", "h", "i", "m", "s"}:
        raise ProtocolError("invalid frame fields")
    m = validate_metadata(obj["m"], limits)
    _hash(obj["s"], "session")
    if sha256(canonical_json(m)) != obj["s"]:
        raise ProtocolError("metadata session mismatch")
    _integer(obj["i"], 0, m["chunks"] - 1, "index")
    _hash(obj["h"], "chunk hash")
    data = b64decode(obj["d"])
    expected = min(m["chunk_size"], m["wire_size"] - obj["i"] * m["chunk_size"])
    if len(data) != expected or sha256(data) != obj["h"]:
        raise ProtocolError("chunk length or hash mismatch")
    return Frame(m, obj["s"], obj["i"], data, obj["h"])


def encode_frame(metadata: dict, index: int, data: bytes) -> str:
    """Canonical frame helper, also validating the resulting frame."""
    obj = {"d": b64encode(data), "h": sha256(data), "i": index,
           "m": metadata, "s": sha256(canonical_json(metadata))}
    payload = PREFIX + b64encode(canonical_json(obj))
    parse_frame(payload)
    return payload
