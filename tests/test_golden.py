"""Language-independent fixtures are consumed by both receiver and sender tests."""
import base64
import json
from pathlib import Path

import pytest

from txqr_restore.protocol import canonical_json, encode_frame, parse_frame, sha256
from txqr_restore.state import ReceiverState

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = json.loads((ROOT / "protocol" / "golden.json").read_text())


@pytest.mark.parametrize("fixture", GOLDEN["fixtures"], ids=lambda f: f["metadata"]["name"])
def test_public_golden_decode_encode_restore(fixture, tmp_path):
    raw = base64.urlsafe_b64decode(fixture["raw_base64url"] + "=" * (-len(fixture["raw_base64url"]) % 4))
    assert sha256(canonical_json(fixture["metadata"])) == fixture["session"]
    state = ReceiverState(tmp_path / "state")
    with state.locked():
        for payload in reversed(fixture["payloads"]):
            frame = parse_frame(payload)
            assert frame.metadata == fixture["metadata"]
            assert frame.session == fixture["session"]
            assert encode_frame(frame.metadata, frame.index, frame.data) == payload
            state.accept(frame)
        assert state.restore(tmp_path / "out").read_bytes() == raw
