# Python receiver

Python 3.10+; official `zxing-cpp` QR decoding, OpenCV video ingestion, NumPy.
Install the pinned dependencies and entry point from the project root:

```sh
python -m venv .venv
.venv/bin/pip install -e './receiver-python[dev]'
.venv/bin/txqr-restore recording-1.mp4 recording-2.mp4 \
  --state ./receive-state --output ./restored --report ./receive-report.json
```

The output argument is a **directory**, not a filename. Only a fully verified
file is published under the metadata basename. Existing files are refused unless
`--overwrite` is explicit. Output must not be inside the state directory; reports
must be outside both state and output directories and must not overwrite an input
video or payload file. Resume with the same state and
additional recordings; scanning all supplied videos enables duplicate statistics
and detects mixed sessions even after all chunks were collected. Use a new state
directory for a different transfer. No custom QR decoder is included.

`--backend zxing` is the default. `--backend opencv` opts into OpenCV's native QR
API (for comparison; relative detection performance is not established by these tests); an individual native decoding exception increments
`decode_errors` and scanning continues. A failure to open/read an input video is
an error, but earlier chunks remain on disk. OpenCV can silently treat damaged
video tails as end-of-stream; integrity verification and the missing-chunk report,
not container frame-count metadata, determine whether restoration is complete.

The JSON report is printed to stdout and optionally atomically saved. `session`
is the full session SHA-256; `missing` contains zero-based chunk indices for the
sender's selective replay. `chunks_present` includes resumed state;
`unique_chunks`, `duplicates`, `invalid_frames`, `decode_errors` and `frames` count
this invocation. `last_invalid_frame_error` gives the last rejected-payload diagnostic without echoing its content. Exit statuses: **0** verified output, **2** incomplete/no usable
session, **1** error (including mixed sessions, conflicting duplicates, corrupted
state, hash failure, blocked overwrite or video-open failure). Malformed QR
payloads are counted and ignored; they cannot produce success by themselves.

`--payload-file FILE` accepts newline-delimited ASCII TXQRT1 payloads for diagnostics
and deterministic tests, and may be repeated or combined with videos. No video
arguments are required when resuming an already complete state directory.

## Bounds and persistence

The receiver rejects duplicate/unknown JSON keys, noncanonical JSON/base64url,
unsafe basenames (including drive colons, Windows device stems and trailing dots/spaces), bool-as-integer fields, invalid sessions, chunk lengths and
hashes. Protocol bounds are 64 MiB each for raw and wire bytes, 4,096-byte chunks,
100,000 chunks and 16,384 ASCII characters per frame. `--max-raw-size`,
`--max-wire-size`, `--max-chunks`, and `--max-chunk-size` can lower, never raise,
these limits. Video defaults/caps are 16,777,216 pixels per frame and 1,000,000
frames per input; lower them with `--max-frame-pixels` and `--max-video-frames`.
Frame dimensions are checked before decoder invocation; the video codec may
allocate memory during frame ingestion. Run untrusted video in an OS sandbox
when stronger codec resource/isolation controls are required.

One process holds an exclusive state lock. Metadata and exact binary chunk bytes
are atomically saved with fsync; filename chunk digests and metadata/session
bounds are revalidated on every resume. Interrupted unpublished temporary writes
are ignored. Never edit or merge state directories by hand: supply more input
videos or payload files instead. The filesystem is assumed locally trusted;
state-directory ancestors and video codec behavior are not an adversarial OS
security boundary.

The entire wire stream is checked for exact size and SHA-256 before gzip decoding.
Gzip reads are bounded by the declared raw size plus one byte; concatenated gzip
members and zero padding are supported, while invalid/truncated members or
non-padding trailing bytes are rejected. Raw size and SHA-256 are then checked before atomic publication.
Temporary restoration files are removed on failure. Chunks are retained for
resumption even after success. Users may delete their state directory when done.

**Hashes are corruption checks, not sender authentication, encryption, or
confidentiality.** A malicious sender can supply another self-consistent transfer.

```sh
cd receiver-python
../.venv/bin/python -m pytest -q
```
