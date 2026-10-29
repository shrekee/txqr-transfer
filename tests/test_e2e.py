"""Real Go QR -> recorded MP4/MOV -> Python ZXing restore acceptance."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from synthetic_video import make_video

ROOT = Path(__file__).resolve().parents[1]


def run(command, expected=0):
    result = subprocess.run([str(x) for x in command], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == expected, f"{command}\nexit={result.returncode}\n{result.stdout}\n{result.stderr}"
    return result


@pytest.fixture(scope="session")
def sender(tmp_path_factory):
    explicit = os.environ.get("TXQR_GO")
    if explicit:
        return Path(explicit)
    go = shutil.which("go")
    if not go:
        pytest.fail("Go compiler required; set TXQR_GO to an already-built txqr-go executable")
    binary = tmp_path_factory.mktemp("bin") / "txqr-go"
    result = subprocess.run([go, "build", "-buildvcs=false", "-o", str(binary), "."], cwd=ROOT / "sender-go", text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return binary


def restore(video, state, output, report, expected=0):
    return run([sys.executable, "-m", "txqr_restore", video, "--state", state,
                "--output", output, "--report", report], expected)


def test_mp4_roundtrip_and_mov_drop_replay_resume(sender, tmp_path):
    raw = b"".join(hashlib.sha256(f"public synthetic sample {i}".encode()).digest() for i in range(30))
    source = tmp_path / "sample.bin"
    source.write_bytes(raw)
    bundle = tmp_path / "bundle"
    run([sender, "send", source, "--out", bundle, "--chunk-size", "128"])
    manifest = json.loads((bundle / "manifest.json").read_text())
    count = manifest["metadata"]["chunks"]
    assert count > 2
    full = make_video(bundle, tmp_path / "full.mp4", list(reversed(range(count))) + [0], repeat=2)
    report = tmp_path / "full-report.json"
    restore(full, tmp_path / "full-state", tmp_path / "full-out", report)
    result = json.loads(report.read_text())
    assert result["missing"] == []
    assert hashlib.sha256((tmp_path / "full-out" / source.name).read_bytes()).digest() == hashlib.sha256(raw).digest()
    missing = [1, count-1]
    partial = make_video(bundle, tmp_path / "partial.mov", [i for i in range(count) if i not in missing], repeat=2)
    state, output, report = tmp_path / "resume-state", tmp_path / "resume-out", tmp_path / "missing.json"
    restore(partial, state, output, report, expected=2)
    first = json.loads(report.read_text())
    assert first["missing"] == missing
    assert first["session"] == manifest["session"]
    assert not (output / source.name).exists()
    replay = tmp_path / "replay"
    run([sender, "replay", bundle, "--missing", report, "--out", replay])
    replay_manifest = json.loads((replay / "manifest.json").read_text())
    assert sorted(entry["index"] for entry in replay_manifest["payloads"]) == missing
    original = {entry["index"]: entry for entry in manifest["payloads"]}
    for entry in replay_manifest["payloads"]:
        for field in ("payload", "qr"):
            assert (replay / entry[field]).read_bytes() == (bundle / original[entry["index"]][field]).read_bytes()
    repair = make_video(replay, tmp_path / "repair.mp4", repeat=2)
    restore(repair, state, output, report)
    assert (output / source.name).read_bytes() == raw
    assert json.loads(report.read_text())["missing"] == []


@pytest.mark.parametrize("raw,compression", [(b"", "none"), (b"public gzip sample\x00\xff" * 500, "gzip")])
def test_go_payload_restore_encoding(sender, tmp_path, raw, compression):
    """Exercise Go gzip byte stream interoperability, including an empty file."""
    source = tmp_path / "encoded.bin"
    source.write_bytes(raw)
    bundle = tmp_path / "bundle"
    run([sender, "send", source, "--out", bundle, "--compression", compression, "--chunk-size", "64"])
    manifest = json.loads((bundle / "manifest.json").read_text())
    payload_list = tmp_path / "payloads.txt"
    payload_list.write_bytes(b"\n".join((bundle / entry["payload"]).read_bytes() for entry in manifest["payloads"]))
    run([sys.executable, "-m", "txqr_restore", "--payload-file", payload_list,
         "--state", tmp_path / "state", "--output", tmp_path / "out"])
    assert (tmp_path / "out" / source.name).read_bytes() == raw
