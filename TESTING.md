# Testing and validation

## Reproduce

Run from the repository root, with a real Go compiler and the pinned Python environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip install -e ./receiver-python --no-deps
(cd sender-go && go test -race ./... && go vet ./...)
python -m pytest receiver-python/tests tests -q
python -m compileall -q receiver-python/src tests
python -m pip check
```

`tests/test_e2e.py` builds the actual Go sender (or uses `$TXQR_GO`), creates fresh deterministic binary input, generates PNG QRs, records them to MP4/MOV using OpenCV's `mp4v` writer, and launches the actual Python CLI in separate subprocesses. Generated fixtures and media live in pytest temporary directories, not in source control. On storage-constrained hosts, set `TMPDIR`, `GOCACHE`, `GOPATH` and `PIP_CACHE_DIR` to a sufficiently large dedicated working directory before installing/testing.

## Verified scope

Validation baseline: Linux x86-64, Go 1.27.3, Python 3.12.14, ZXing-C++ 2.3.0, OpenCV wheel 4.14.0.94, NumPy 2.1.3, pytest 8.3.4. The tested OpenCV build reports libavcodec 62.28.102 and libavformat 62.12.102.

- Independent canonical protocol fixtures are consumed by Go and Python, including binary bytes, an empty file, and literal ASCII HTML-special filename characters (for canonical-JSON escaping).
- Go-generated gzip bytes and empty-file payloads restore through Python to exact original bytes.
- Actual Go PNGs decode from a reordered/duplicated MP4 into the exact original SHA-256.
- A MOV with two chunks deliberately omitted yields exit 2 and the exact zero-based missing list. Replay copies the original payloads and QR PNGs byte-for-byte; a second recorded MP4 fills the existing state in a new process and restores the exact original bytes.
- Unit tests cover strict parsing, metadata limits, invalid sizes/types/base64/JSON, duplicate JSON keys, session identity, mixed sessions, conflicting duplicates, corrupted state, restart, gzip truncation/trailing data/bombs, safe output paths, no-overwrite, report-path collisions, per-frame decoder exceptions and retained progress.
- Go tests validate actual library capacity errors, preset precedence, pixel/module sizing, immutable replay, deterministic compression and canonical encoding. The race detector and vet are run separately.

## Observed local results (2026-10-29)

- Combined receiver, golden and cross-language/video suite: **77 passed**.
- Go unit suite (8 tests), race detector and vet: **passed**.
- Python compileall and dependency consistency (`pip check`): **passed**.
- Release packaging helper tests: run separately by the documented release validation workflow.

These are local results for the stated baseline, not a substitute for the CI result on a particular published commit.

## Not established by these tests

No physical display-to-camera trial has been run. A real-browser interaction smoke test of the offline player was not established in this environment; embedded assets/no-external-resource generation is covered by a Go test. These tests do **not** establish real camera reliability, maximum throughput, resistance to adversarial visual input, native decoder memory safety, or uniform MP4/MOV codec availability. OpenCV alternate-backend error isolation is unit-tested; the reference optical acceptance backend is ZXing-C++. Cross-compiled Go binaries are not evidence that all target operating systems have been exercised; native release smoke results are tracked by the release workflow.

The public protocol is independent; no earlier TXQR format compatibility is tested or claimed. SHA-256 is integrity checking, not authentication or confidentiality.
