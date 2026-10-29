"""Crash-resumable state and atomic, hash-verified restoration."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import zlib
from contextlib import contextmanager

from .protocol import (Frame, Limits, ProtocolError, canonical_json, sha256,
                       strict_json, validate_metadata)


class StateError(RuntimeError):
    pass


class SessionConflict(StateError):
    pass


class Incomplete(StateError):
    pass


CHUNK_NAME = re.compile(r"([0-9]{6})-([0-9a-f]{64})\.chunk\Z")


def _fsync_dir(path: Path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_write(path: Path, data: bytes):
    """Replace one owned file atomically, with data and directory durability."""
    fd, tmp = tempfile.mkstemp(prefix=".txqrt-tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _bounded_regular_read(path: Path, limit: int) -> bytes:
    if path.is_symlink():
        raise StateError("state file must not be a symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise StateError(f"unsafe or oversized state file: {path.name}")
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise StateError("oversized state file")
        return data


class ReceiverState:
    """One session per state directory. Callers should hold ``locked()``."""

    def __init__(self, directory: str | Path, limits: Limits = Limits()):
        self.directory = Path(directory)
        self.limits = limits
        self.metadata = None
        self.session = None
        self.chunks: dict[int, Path] = {}
        if self.directory.is_symlink():
            raise StateError("state directory must not be a symlink")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.chunk_dir = self.directory / "chunks"
        if self.chunk_dir.is_symlink():
            raise StateError("chunk directory must not be a symlink")
        self.chunk_dir.mkdir(exist_ok=True)

    @contextmanager
    def locked(self):
        lock_path = self.directory / ".lock"
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise StateError("state lock is not a regular file")
            if os.name == "nt":
                import msvcrt
                os.write(fd, b"0")
                os.lseek(fd, 0, 0)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.load()
            yield self
        finally:
            os.close(fd)

    def load(self):
        self.metadata = None
        self.session = None
        self.chunks = {}
        metadata_file = self.directory / "session.json"
        if metadata_file.exists() or metadata_file.is_symlink():
            obj = strict_json(_bounded_regular_read(metadata_file, 4096))
            if type(obj) is not dict or set(obj) != {"m", "s"}:
                raise StateError("invalid saved session")
            self.metadata = validate_metadata(obj["m"], self.limits)
            self.session = sha256(canonical_json(self.metadata))
            if obj["s"] != self.session:
                raise StateError("saved metadata session mismatch")
        # Iterate without constructing a giant directory listing.
        with os.scandir(self.chunk_dir) as entries:
            for entry in entries:
                if entry.name.startswith(".txqrt-tmp-"):
                    continue  # An interrupted pre-publication write is never accepted.
                match = CHUNK_NAME.fullmatch(entry.name)
                if not match or self.metadata is None:
                    raise StateError("unexpected chunk state entry")
                index, digest = int(match[1]), match[2]
                if index >= self.metadata["chunks"] or index in self.chunks:
                    raise StateError("duplicate or out-of-range saved chunk")
                data = _bounded_regular_read(Path(entry.path), self.metadata["chunk_size"])
                expected = min(self.metadata["chunk_size"], self.metadata["wire_size"] - index * self.metadata["chunk_size"])
                if len(data) != expected or sha256(data) != digest:
                    raise StateError("saved chunk failed length/hash validation")
                self.chunks[index] = Path(entry.path)

    def accept(self, frame: Frame) -> bool:
        """Persist a parsed frame. Return True if new, False if identical duplicate."""
        validate_metadata(frame.metadata, self.limits)
        if (frame.session != sha256(canonical_json(frame.metadata))
                or type(frame.index) is not int or not 0 <= frame.index < frame.metadata["chunks"]
                or type(frame.data) is not bytes or sha256(frame.data) != frame.chunk_hash
                or len(frame.data) != min(frame.metadata["chunk_size"], frame.metadata["wire_size"] - frame.index * frame.metadata["chunk_size"])):
            raise ProtocolError("unvalidated frame supplied to state")
        if self.session is not None and (frame.session != self.session or frame.metadata != self.metadata):
            raise SessionConflict("mixed sessions: use a separate --state directory")
        if self.session is None:
            self.metadata = dict(frame.metadata)
            self.session = frame.session
            atomic_write(self.directory / "session.json", canonical_json({"m": self.metadata, "s": self.session}))
        existing = self.chunks.get(frame.index)
        if existing is not None:
            if _bounded_regular_read(existing, self.metadata["chunk_size"]) != frame.data:
                raise SessionConflict(f"conflicting duplicate chunk {frame.index}")
            return False
        path = self.chunk_dir / f"{frame.index:06d}-{frame.chunk_hash}.chunk"
        atomic_write(path, frame.data)
        self.chunks[frame.index] = path
        return True

    @property
    def missing(self) -> list[int]:
        return [] if self.metadata is None else [i for i in range(self.metadata["chunks"]) if i not in self.chunks]

    def restore(self, output_directory: str | Path, overwrite: bool = False) -> Path:
        if self.metadata is None or self.missing:
            raise Incomplete("no session or missing chunks")
        output = Path(output_directory)
        output.mkdir(parents=True, exist_ok=True)
        # Metadata permits printable ASCII only and disallows both path separators.
        target = output / self.metadata["name"]
        if target.exists() or target.is_symlink():
            if not overwrite:
                raise StateError(f"output already exists: {target}; pass --overwrite to replace")
            if target.is_dir():
                raise StateError("cannot overwrite a directory")
        wire_fd, wire_name = tempfile.mkstemp(prefix=".txqrt-tmp-wire-", dir=self.directory)
        raw_fd, raw_name = tempfile.mkstemp(prefix=".txqrt-tmp-raw-", dir=output)
        try:
            wire_hash, wire_size = hashlib.sha256(), 0
            with os.fdopen(wire_fd, "wb") as wire:
                for index in range(self.metadata["chunks"]):
                    data = _bounded_regular_read(self.chunks[index], self.metadata["chunk_size"])
                    # Recheck files after loading; changes must never bypass verification.
                    if sha256(data) != CHUNK_NAME.fullmatch(self.chunks[index].name)[2]:
                        raise StateError("saved chunk changed during restore")
                    wire.write(data)
                    wire_hash.update(data)
                    wire_size += len(data)
            if wire_size != self.metadata["wire_size"] or wire_hash.hexdigest() != self.metadata["wire_sha256"]:
                raise StateError("complete wire size/hash mismatch")
            raw_hash, raw_size = hashlib.sha256(), 0
            with os.fdopen(raw_fd, "wb") as raw, open(wire_name, "rb") as wire:
                source = gzip.GzipFile(fileobj=wire) if self.metadata["encoding"] == "gzip" else wire
                try:
                    while True:
                        # A bounded read detects a bomb at expected size + 1 byte.
                        data = source.read(min(65536, self.metadata["raw_size"] - raw_size + 1))
                        if not data:
                            break
                        raw_size += len(data)
                        if raw_size > self.metadata["raw_size"]:
                            raise StateError("decoded output exceeds declared raw size")
                        raw_hash.update(data)
                        raw.write(data)
                except (OSError, EOFError, ValueError, zlib.error) as exc:
                    raise StateError(f"invalid compressed content: {exc}") from exc
                finally:
                    if source is not wire:
                        source.close()
                if raw_size != self.metadata["raw_size"] or raw_hash.hexdigest() != self.metadata["raw_sha256"]:
                    raise StateError("raw size/hash mismatch")
                raw.flush()
                os.fsync(raw.fileno())
            if overwrite:
                os.replace(raw_name, target)
            else:
                # Link publishes without clobbering a file created after the check.
                os.link(raw_name, target)
                os.unlink(raw_name)
            _fsync_dir(output)
            return target
        finally:
            # fdopen may not have been reached when an earlier validation fails.
            for fd in (wire_fd, raw_fd):
                try:
                    os.close(fd)
                except OSError:
                    pass
            for name in (wire_name, raw_name):
                if os.path.exists(name):
                    os.unlink(name)
