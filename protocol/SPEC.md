# TXQRT1 wire protocol

This is a newly specified, independent protocol. Compatibility with earlier TXQR implementations is not claimed. Receivers accept exactly the public format below and reject other formats or versions.

Every QR is ASCII `TXQRT1:` followed by unpadded URL-safe base64 of a canonical JSON object with exactly the keys `d,h,i,m,s`.
Canonical JSON is UTF-8, keys lexicographically sorted, separators `,` and `:`, no whitespace. Strings are ASCII, using literal printable ASCII except JSON-required quote/backslash escapes; HTML characters are not escaped. Unknown keys and duplicate JSON keys are rejected.

`m` is metadata with exactly these fields:
- `v`: integer 1
- `name`: 1..128 printable ASCII characters, excluding `/`, `\\`, `:`, control characters; not `.` or `..`, no trailing dot/space, and no case-insensitive Windows device stem (`CON`, `PRN`, `AUX`, `NUL`, `COM1`..`COM9`, `LPT1`..`LPT9`, before the first dot, with trailing stem spaces removed). A filename, never a path.
- `raw_size`: integer 0..67108864
- `raw_sha256`: lowercase 64-hex SHA-256 of original file
- `encoding`: `none` or `gzip`
- `wire_size`: integer 0..67108864
- `wire_sha256`: lowercase 64-hex SHA-256 of exact transmitted bytes
- `chunk_size`: integer 1..4096
- `chunks`: integer 1..100000, exactly max(1, ceil(wire_size/chunk_size))

`s` is lowercase hex SHA-256 of canonical JSON `m`. Thus compressed bytes, layout, filename and original hash bind session identity. `i` is a zero-based chunk index. `d` is unpadded URL-safe base64 of immutable exact chunk bytes. `h` is lowercase hex SHA-256 of those bytes. Non-final chunks have exactly chunk_size bytes; final chunk has the exact remaining length. An empty file has one empty chunk. For encoding none, raw_size = wire_size and raw_sha256 = wire_sha256. A frame is at most 16384 ASCII characters. Base64 is canonical: reject padding, alternate spelling and non URL-safe characters.

Protocol hard limits apply before persistence/allocation. Receivers may configure smaller raw/wire limits. Booleans are not integers. Metadata and payload validation is mandatory on resume as well as new frames. Receivers reject mixed sessions and conflicting duplicates (same session/index, different bytes). A corrupt frame must not destroy previously accepted chunks.

Reassembly concatenates chunks by index, verifies exact wire size and wire SHA-256, then bounded decompression (if gzip) and exact raw size/SHA-256, before atomic output publication. No output overwrite without an explicit flag. Missing reports list zero-based indices. Replay uses the existing exact payloads, never repacks data.

Hashes detect accidental damage, not forgery: the protocol supplies neither authentication nor confidentiality. Only an independently trusted digest can establish authenticity. No legacy decoder or fountain/FEC layer is included.
