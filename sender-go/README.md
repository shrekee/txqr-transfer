# TXQR Transfer Go sender

Fresh implementation of the public `TXQRT1` protocol in `../protocol/SPEC.md`. It generates and accepts only the exact `TXQRT1:` format prefix. Go 1.22 or newer is required.

```sh
go build -buildvcs=false -o txqr-go .
./txqr-go send example.bin --out transfer
# Open transfer/player.html in an offline browser and record its QR slideshow.
./txqr-go replay transfer --out replay --missing missing.json
./txqr-go replay transfer/manifest.json --out replay2 --missing 0,2,5
```

`--out` must name a directory that does not exist. Its parent must exist. On a failed send/replay, the new partial directory is removed. Existing outputs are never overwritten. Keep the source transfer directory for exact replay.

## Send flags

Flags can come before or after the input filename. `send --help` lists them.

| Flag | Default | Meaning |
|---|---:|---|
| `--preset` | conservative | conservative, balanced, or fast |
| `--chunk-size` | preset | 1..4096 transmitted bytes per frame |
| `--ecc` | preset | QR error correction L, M, Q, H |
| `--qr-version` | 0 | 0 selects automatically; otherwise force 1..40 |
| `--scale` | preset | integer pixels per QR module, including quiet zone |
| `--pixels` | 0 | exact PNG width instead of scale; 0 uses scale |
| `--fps` | preset | offline player frames/second, greater than 0 and at most 60 |
| `--compression` | none | none, or deterministic gzip |
| `--name` | input basename | transmitted printable ASCII basename, 1..128 bytes |
| `--out` | required | new output directory |

Preset values:

| Preset | Chunk bytes | ECC | FPS | Scale |
|---|---:|---|---:|---:|
| conservative | 256 | M | 2 | 6 |
| balanced | 512 | M | 4 | 5 |
| fast | 768 | L | 8 | 4 |

Precedence is explicit flag > selected preset > defaults. Presets are starting points, not guarantees for a particular camera/display. Forced versions are checked by the actual QR encoder. Larger PNGs **do not** increase QR capacity. Exact `--pixels` must be a positive multiple of the actual module width; an error reports that width. `--scale` and `--pixels` cannot both be explicitly supplied. The quiet zone is always retained. PNGs are limited to 8192 pixels per side.

Input must be a regular file, at most 64 MiB. Filename path separators, colons, controls, non-ASCII names, trailing dots/spaces, `.`/`..`, and Windows device stems (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`, case-insensitive, before the first dot, ignoring stem suffix spaces) are rejected; use `--name` for another transmitted basename. The protocol permits printable ASCII, including `<`, `>`, `&` and quotes; canonical encoding only escapes JSON-required characters. Compression header timestamps are fixed and do not include source filenames. Session identity binds the exact resulting gzip bytes; never recompress a transfer to fill missing frames.

## Output contract

- `payloads/000000.txt`: exact immutable ASCII QR content, no trailing newline.
- `qr/000000.png`: QR raster generated from the corresponding payload.
- `manifest.json`: format `TXQRT1`, full `session` hex, protocol `metadata`, player `fps`, `ecc`, and a `payloads` array.
- `player.html`: fully standalone offline slideshow. It embeds the original PNG bytes, needs no web server, JavaScript library, CDN, network or local-file fetch. Pause/play, previous/next and FPS controls are included.

Each manifest `payloads` item has `index`, relative `payload` and `qr` paths, `payload_sha256`, `qr_sha256`, actual `qr_version`, and actual `pixels`. Metadata contains the exact protocol `m` fields. The session is SHA-256 of sorted-key, compact canonical metadata JSON. Source contents and PNGs are never repacked during replay.

Standalone HTML duplicates PNG data as base64, so it can be substantially larger than the original input. Large transfers can take significant disk space and browser memory. The sender streams HTML generation rather than buffering the entire slideshow in memory.

## Missing-only replay

A receiver report has at least:

```json
{"session":"<full 64-character session hash>","missing":[0,2,5]}
```

A report session mismatch, missing/out-of-range/duplicate indices, noncanonical payload, changed payload/QR hash, unsafe path, or unavailable frame is an error. An empty missing list is reported as nothing to replay. A comma list is available when a report is inconvenient; it cannot carry a session check. Omitting `--missing` replays all frames present in the source manifest.

Replay copies the exact original payload and PNG files. It never rereads the input, regenerates a QR, changes compression, or changes chunk layout. A replay manifest only lists selected entries; its metadata and session still describe the complete original transfer. A replay can itself be replayed, but only for frames it contains.

## Verification and dependency

```sh
go test -v ./...
go vet ./...
```

Tests cover the independent language-neutral golden fixture, ASCII canonical encoding, empty files, deterministic gzip, preset precedence, capacity vs pixel size, source immutability, selected replay, report session mismatch, corrupt source rejection, and interspersed CLI flags.

The only Go dependency is the official `github.com/skip2/go-qrcode`, pinned to `v0.0.0-20200617195104-da1b6568686e` in `go.mod`/`go.sum`. Its upstream license is MIT; see its source module license. No new project license is applied by this sender implementation.

Hashes detect accidental corruption, not malicious forgery. This format supplies neither confidentiality nor authentication; use an independently trusted original digest if authenticity matters.
