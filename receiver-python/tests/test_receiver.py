import gzip
import json
from pathlib import Path
import sys
import types

import pytest

from txqr_restore.cli import main
from txqr_restore.protocol import (Limits, PREFIX, ProtocolError, b64encode, canonical_json,
                                   encode_frame, parse_frame, sha256)
from txqr_restore.scanner import Stats, VideoError, accept_payload, scan_video
from txqr_restore.state import Incomplete, ReceiverState, SessionConflict, StateError


def fixture(raw=b"hello transfer", chunk_size=4, encoding="none", name="hello.bin", wire=None):
    if wire is None:
        wire = gzip.compress(raw, mtime=0) if encoding == "gzip" else raw
    m = {"v": 1, "name": name, "raw_size": len(raw), "raw_sha256": sha256(raw),
         "encoding": encoding, "wire_size": len(wire), "wire_sha256": sha256(wire),
         "chunk_size": chunk_size, "chunks": max(1, (len(wire) + chunk_size - 1) // chunk_size)}
    return m, [encode_frame(m, i, wire[i * chunk_size:(i + 1) * chunk_size]) for i in range(m["chunks"])]


def envelope(payload):
    import base64
    return json.loads(base64.urlsafe_b64decode(payload[len(PREFIX):] + "=" * (-len(payload[len(PREFIX):]) % 4)))


def wrap(obj):
    return PREFIX + b64encode(canonical_json(obj))


@pytest.mark.parametrize("raw,encoding", [(b"", "none"), (b"abc", "none"), (bytes(range(256)) * 200, "gzip")])
def test_roundtrip_resume_duplicates(tmp_path, raw, encoding):
    m, payloads = fixture(raw, encoding=encoding)
    state = ReceiverState(tmp_path / "state")
    with state.locked():
        for payload in payloads[::2]:
            assert state.accept(parse_frame(payload))
            assert not state.accept(parse_frame(payload))
    resumed = ReceiverState(tmp_path / "state")
    with resumed.locked():
        for payload in payloads[1::2]:
            resumed.accept(parse_frame(payload))
        result = resumed.restore(tmp_path / "output")
        assert result.read_bytes() == raw
        with pytest.raises(StateError, match="already exists"):
            resumed.restore(tmp_path / "output")
        assert resumed.restore(tmp_path / "output", overwrite=True).read_bytes() == raw


@pytest.mark.parametrize("field,value", [("v", True), ("raw_size", True), ("chunk_size", 0),
    ("chunks", 100001), ("raw_size", 67108865), ("wire_size", -1), ("name", "../x"),
    ("name", ".."), ("name", "x\\y"), ("name", "x\n"), ("name", "é"), ("name", "a" * 129),
    ("raw_sha256", "A" * 64), ("encoding", "zip"), ("chunks", 99), ("extra", 1)])
def test_metadata_rejections(field, value):
    _, payloads = fixture()
    obj = envelope(payloads[0])
    obj["m"][field] = value
    obj["s"] = sha256(canonical_json(obj["m"]))
    with pytest.raises(ProtocolError):
        parse_frame(wrap(obj))


@pytest.mark.parametrize("mutate", [lambda o: o.update(i=True), lambda o: o.update(i=-1),
    lambda o: o.update(i=999), lambda o: o.update(s="0" * 64), lambda o: o.update(h="0" * 64),
    lambda o: o.update(d=o["d"] + "="), lambda o: o.update(d="AB"), lambda o: o.update(extra=1)])
def test_frame_rejections(mutate):
    _, payloads = fixture()
    obj = envelope(payloads[0]); mutate(obj)
    with pytest.raises(ProtocolError):
        parse_frame(wrap(obj))


def test_canonical_and_size_rejections():
    _, payloads = fixture()
    obj = envelope(payloads[0])
    duplicate = canonical_json(obj).replace(b'"i":0', b'"i":0,"i":0')
    for payload in ["OTHER1:x", payloads[0] + "=", "TXQRT1:" + "a" * 16384,
                    PREFIX + b64encode(duplicate), PREFIX + b64encode(json.dumps(obj).encode()),
                    PREFIX + "_w", b"TXQRT1:\xff", PREFIX + b64encode(b"[" * 2000)]:
        with pytest.raises(ProtocolError):
            parse_frame(payload)


def test_smaller_limits_only():
    for kwargs in [{"max_raw_size": 67108865}, {"max_chunks": 100001}, {"max_chunk_size": 4097},
                   {"max_raw_size": True}, {"max_chunks": 0}]:
        with pytest.raises(ValueError):
            Limits(**kwargs)
    _, payloads = fixture()
    with pytest.raises(ProtocolError):
        parse_frame(payloads[0], Limits(max_raw_size=1))


def test_mixed_and_conflicting_preserve_progress(tmp_path):
    m, payloads = fixture()
    _, other = fixture(b"different transfer")
    state = ReceiverState(tmp_path)
    with state.locked():
        state.accept(parse_frame(payloads[0]))
        with pytest.raises(SessionConflict, match="mixed"):
            state.accept(parse_frame(other[0]))
        obj = envelope(payloads[0]); obj["d"] = b64encode(b"xxxx"); obj["h"] = sha256(b"xxxx")
        with pytest.raises(SessionConflict, match="conflicting"):
            state.accept(parse_frame(wrap(obj)))
    resumed = ReceiverState(tmp_path)
    with resumed.locked():
        assert len(resumed.chunks) == 1
        assert resumed.chunks[0].read_bytes() == b"hell"


@pytest.mark.parametrize("corruption", ["bytes", "index", "symlink", "metadata", "oversize"])
def test_corrupt_state_rejected(tmp_path, corruption):
    _, payloads = fixture()
    state = ReceiverState(tmp_path / "state")
    with state.locked():
        state.accept(parse_frame(payloads[0]))
    chunk = next(state.chunk_dir.glob("*.chunk"))
    if corruption == "bytes": chunk.write_bytes(b"oops")
    elif corruption == "index": chunk.rename(chunk.with_name("999999-" + chunk.name.split("-", 1)[1]))
    elif corruption == "symlink":
        chunk.unlink(); chunk.symlink_to(tmp_path / "nonexistent")
    elif corruption == "metadata": (state.directory / "session.json").write_bytes(b"{}")
    else: (state.directory / "session.json").write_bytes(b"x" * 4097)
    with pytest.raises((StateError, ProtocolError, OSError)):
        with ReceiverState(state.directory).locked():
            pass


@pytest.mark.parametrize("encoding,raw,wire", [
    ("gzip", b"a", gzip.compress(b"a" * 100000)),
    ("gzip", b"abc", b"not gzip"),
    ("gzip", b"abc", gzip.compress(b"abc")[:-2]),
    ("gzip", b"abc", gzip.compress(b"abc") + b"garbage"),
    ("gzip", b"wrong", gzip.compress(b"other")),
])
def test_bounded_gzip_and_integrity_never_publish(tmp_path, encoding, raw, wire):
    _, payloads = fixture(raw, encoding=encoding, wire=wire)
    state = ReceiverState(tmp_path / "state")
    with state.locked():
        for payload in payloads: state.accept(parse_frame(payload))
        with pytest.raises(StateError): state.restore(tmp_path / "output")
    assert list((tmp_path / "output").iterdir()) == []


def test_concatenated_gzip_supported(tmp_path):
    _, payloads = fixture(b"ab", encoding="gzip", wire=gzip.compress(b"a") + gzip.compress(b"b"))
    state = ReceiverState(tmp_path / "state")
    with state.locked():
        for payload in payloads: state.accept(parse_frame(payload))
        assert state.restore(tmp_path / "out").read_bytes() == b"ab"


def test_final_wire_hash_required(tmp_path):
    _, payloads = fixture()
    state = ReceiverState(tmp_path / "state")
    with state.locked():
        for index, payload in enumerate(payloads):
            obj = envelope(payload)
            if index == 0:
                obj["d"] = b64encode(b"xxxx"); obj["h"] = sha256(b"xxxx")
            state.accept(parse_frame(wrap(obj)))
        with pytest.raises(StateError, match="wire"):
            state.restore(tmp_path / "output")
    assert not (tmp_path / "output" / "hello.bin").exists()


def test_cli_incomplete_resume_report_and_open_failure(tmp_path, capsys):
    _, payloads = fixture()
    path = tmp_path / "payloads"
    path.write_text(payloads[0] + "\ninvalid\n")
    args = ["--state", str(tmp_path / "state"), "--output", str(tmp_path / "out"),
            "--payload-file", str(path), "--report", str(tmp_path / "report.json")]
    assert main(args) == 2
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["session"] and report["missing"] == list(range(1, len(payloads)))
    assert report["unique_chunks"] == 1 and report["invalid_frames"] == 1
    assert main([str(tmp_path / "missing.mp4"), *args]) == 1
    assert json.loads((tmp_path / "report.json").read_text())["chunks_present"] == 1
    path.write_text("\n".join(payloads[1:]))
    assert main(args) == 0
    assert (tmp_path / "out" / "hello.bin").read_bytes() == b"hello transfer"
    capsys.readouterr()


def test_malformed_only_not_success(tmp_path, capsys):
    path = tmp_path / "payloads"; path.write_bytes(b"invalid\n" + b"x" * 20000 + b"\n")
    assert main(["--state", str(tmp_path / "state"), "--output", str(tmp_path / "out"), "--payload-file", str(path)]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["invalid_frames"] == 2 and report["session"] is None


def mock_cv2(monkeypatch, payloads, errors=(), too_large=False):
    import numpy as np
    frames = iter(range(len(payloads)))
    class CvError(Exception): pass
    class Capture:
        def isOpened(self): return True
        def get(self, _): return 0
        def read(self):
            try: i = next(frames)
            except StopIteration: return False, None
            return True, np.full((2, 2, 3) if not too_large else (100, 100, 3), i, dtype=np.uint8)
        def release(self): pass
    class Detector:
        def detectAndDecodeMulti(self, pixels):
            i = int(pixels[0, 0, 0])
            if i in errors: raise CvError("bad frame")
            return True, [payloads[i]], None, None
    cv2 = types.SimpleNamespace(error=CvError, VideoCapture=lambda _: Capture(), QRCodeDetector=Detector,
                                CAP_PROP_FRAME_WIDTH=3, CAP_PROP_FRAME_HEIGHT=4)
    monkeypatch.setitem(sys.modules, "cv2", cv2)


def test_opencv_decode_exception_isolated_and_resumable(tmp_path, monkeypatch):
    _, payloads = fixture(b"abcdefgh", chunk_size=4)
    mock_cv2(monkeypatch, [payloads[0], "bad", payloads[1]], errors=(1,))
    state = ReceiverState(tmp_path / "state"); stats = Stats()
    with state.locked():
        scan_video("mock", state, stats, backend="opencv")
        assert len(state.chunks) == 2 and stats.decode_errors == 1
        assert state.restore(tmp_path / "out").read_bytes() == b"abcdefgh"


def test_opencv_session_error_not_swallowed(tmp_path, monkeypatch):
    _, one = fixture(b"one"); _, two = fixture(b"two")
    mock_cv2(monkeypatch, [one[0], two[0]])
    state = ReceiverState(tmp_path / "state"); stats = Stats()
    with state.locked():
        with pytest.raises(SessionConflict): scan_video("mock", state, stats, backend="opencv")
        assert len(state.chunks) == 1 and stats.decode_errors == 0


def test_video_resource_limits(tmp_path, monkeypatch):
    _, payloads = fixture()
    mock_cv2(monkeypatch, payloads, too_large=True)
    state = ReceiverState(tmp_path / "state")
    with state.locked():
        with pytest.raises(VideoError, match="pixel"):
            scan_video("mock", state, Stats(), backend="opencv", max_frame_pixels=4)
    mock_cv2(monkeypatch, payloads)
    with state.locked():
        with pytest.raises(VideoError, match="frame count"):
            scan_video("mock", state, Stats(), backend="opencv", max_video_frames=1)
        assert len(state.chunks) == 1


@pytest.mark.parametrize("name", ["C:foo", "CON", "con.txt", "PRN", "AUX.txt", "NUL", "COM1", "lpt9.dat", "CON .txt", "trailing.", "trailing "])
def test_portable_filename_rejections(name):
    with pytest.raises(ProtocolError):
        fixture(name=name)


def test_direct_unvalidated_frame_rejected(tmp_path):
    from txqr_restore.protocol import Frame
    m, _ = fixture()
    state = ReceiverState(tmp_path)
    with state.locked():
        with pytest.raises(ProtocolError):
            state.accept(Frame(m, "0" * 64, 0, b"hell", sha256(b"hell")))
        assert state.session is None


@pytest.mark.parametrize("mode", ["state-report", "output-report", "state-output"])
def test_cli_path_collisions_rejected(tmp_path, mode, capsys):
    _, payloads = fixture()
    payload = tmp_path / "payload"; payload.write_text("\n".join(payloads))
    state = tmp_path / "state"; out = tmp_path / "out"; report = tmp_path / "report"
    if mode == "state-report": report = state / "session.json"
    elif mode == "output-report": report = out / "hello.bin"
    else: out = state / "nested"
    assert main(["--state", str(state), "--output", str(out), "--report", str(report), "--payload-file", str(payload)]) == 1
    assert not (out / "hello.bin").exists()
    capsys.readouterr()


@pytest.mark.parametrize("failure", ["missing-video", "mixed-payload", "bad-state", "bad-limit"])
def test_report_never_clobbers_output_on_error(tmp_path, failure, capsys):
    _, payloads = fixture()
    state_dir, out = tmp_path / "state", tmp_path / "out"
    state = ReceiverState(state_dir)
    with state.locked():
        for payload in payloads: state.accept(parse_frame(payload))
        result = state.restore(out)
    payload = tmp_path / "payload"; payload.write_text("other")
    args = ["--state", str(state_dir), "--output", str(out), "--report", str(result)]
    if failure == "missing-video": args.insert(0, str(tmp_path / "missing.mp4"))
    elif failure == "mixed-payload":
        _, other = fixture(b"other")
        payload.write_text(other[0]); args += ["--payload-file", str(payload)]
    elif failure == "bad-state": (state_dir / "session.json").write_text("bad")
    else: args += ["--max-raw-size", "-1"]
    assert main(args) == 1
    assert result.read_bytes() == b"hello transfer"
    capsys.readouterr()


@pytest.mark.parametrize("source_kind", ["payload", "video", "hardlink"])
def test_report_cannot_clobber_input(tmp_path, source_kind, capsys):
    source = tmp_path / "input"; source.write_bytes(b"keep me")
    report = source
    if source_kind == "hardlink":
        report = tmp_path / "alias"; report.hardlink_to(source)
    args = ["--state", str(tmp_path / "state"), "--output", str(tmp_path / "out"), "--report", str(report)]
    if source_kind == "video": args.insert(0, str(source))
    else: args += ["--payload-file", str(source)]
    assert main(args) == 1
    assert source.read_bytes() == b"keep me" and report.read_bytes() == b"keep me"
    capsys.readouterr()


def test_report_write_failure_returns_error_status(tmp_path, capsys):
    report = tmp_path / "report-dir"; report.mkdir()
    assert main(["--state", str(tmp_path / "state"), "--output", str(tmp_path / "out"), "--report", str(report)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error" and "cannot write report" in result["error"]


def test_gzip_zero_padding_supported(tmp_path):
    _, payloads = fixture(b"ab", encoding="gzip", wire=gzip.compress(b"ab") + b"\x00" * 20)
    state = ReceiverState(tmp_path / "state")
    with state.locked():
        for payload in payloads: state.accept(parse_frame(payload))
        assert state.restore(tmp_path / "out").read_bytes() == b"ab"
