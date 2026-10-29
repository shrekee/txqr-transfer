"""Regenerate public synthetic protocol fixtures without importing either implementation."""
import base64
import hashlib
import json
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def fixture(name, raw, chunk_size):
    digest = hashlib.sha256(raw).hexdigest()
    meta = dict(v=1, name=name, raw_size=len(raw), raw_sha256=digest, encoding="none",
                wire_size=len(raw), wire_sha256=digest, chunk_size=chunk_size,
                chunks=max(1, (len(raw) + chunk_size - 1) // chunk_size))
    session = hashlib.sha256(canonical(meta)).hexdigest()
    payloads = []
    for i in range(meta["chunks"]):
        chunk = raw[i * chunk_size:(i + 1) * chunk_size]
        frame = dict(d=b64(chunk), h=hashlib.sha256(chunk).hexdigest(), i=i, m=meta, s=session)
        payloads.append("TXQRT1:" + b64(canonical(frame)))
    return dict(description="Fresh synthetic public fixture, no private input", metadata=meta,
                session=session, raw_base64url=b64(raw), payloads=payloads)


if __name__ == "__main__":
    output = Path(__file__).resolve().parents[1] / "protocol" / "golden.json"
    output.write_text(json.dumps(dict(format="TXQRT1", fixtures=[
        fixture("hello.bin", b"TXQRT1 public fixture\x00\xff\n", 8),
        fixture("empty.bin", b"", 16),
        fixture("ascii<&>.bin", b"canonical ASCII", 64),
    ]), indent=2) + "\n", encoding="utf-8")
