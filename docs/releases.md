# Release process

Release assets contain the Go **sender**. The Python receiver is delivered as
source in GitHub's automatically generated source archives; it is not a bundled
or single-file Python executable. Use the pinned environment/install instructions
in the README from the selected tag.

## Assets and supported build targets

| Target | Native GitHub-hosted runner | Archive |
| --- | --- | --- |
| Linux amd64 | `ubuntu-24.04` | `txqr-transfer_<tag>_linux_amd64.tar.gz` |
| Linux arm64 | `ubuntu-24.04-arm` | `txqr-transfer_<tag>_linux_arm64.tar.gz` |
| macOS arm64 / Apple Silicon | `macos-14` | `txqr-transfer_<tag>_darwin_arm64.tar.gz` |
| Windows amd64 | `windows-2022` | `txqr-transfer_<tag>_windows_amd64.zip` |

Each archive has root-level `txqr-go` (`txqr-go.exe` on Windows), `LICENSE`,
`README.md`, `THIRD_PARTY.md`, and `BUILD-INFO`. BUILD-INFO identifies the exact
numeric tag, full source commit, OS/CPU, Go compiler, and CGO setting. Archive
executables have Unix mode 0755 and documents 0644. Archives use sorted entries,
fixed timestamps, and fixed ownership; the helper checks byte-for-byte repeat
packaging and same-runner repeat Go builds. This is not a claim of reproducibility
across different compiler versions, Python/zlib versions, or operating systems.

The workflow pins Go **1.27.3**, builds with `CGO_ENABLED=0`, `-mod=readonly`,
`-trimpath`, and `-buildvcs=false`. These pure-Go sender binaries do not include
FFmpeg or the Python decoder. Go's minimum runtime OS requirements still apply;
only the listed runner OS versions are exercised. macOS binaries are unsigned
and not notarized; Windows binaries are unsigned. No x86 macOS or Windows arm64
asset is offered.

## First authorized release: exact tag `0.1.0`

1. Publish the reviewed source and release tooling to `main`; record its complete
   40-character lowercase source commit SHA. Wait for CI on that exact commit.
2. In a **separate commit** on `main`, add
   `.github/release-requests/0.1.0.json` with exactly two fields:

   ```json
   {"tag":"0.1.0","commit":"REPLACE_WITH_FULL_40_CHARACTER_SOURCE_COMMIT_SHA"}
   ```

   The placeholder is intentionally invalid: use the actual source SHA, not a
   branch name, abbreviation, or the trigger commit's SHA. The source must be an
   ancestor of the trigger commit. Do not edit or move the source after review.
3. Push that request commit. The Release workflow validates it, tests the source,
   and builds all four native assets before the publish job may create the
   lightweight `0.1.0` tag at the **source commit**, not the request commit.
4. Watch every job. The publish job creates a draft Release, uploads the assets
   and `SHA256SUMS`, downloads every uploaded asset through the GitHub API, and
   independently verifies all bytes and checksums before publishing the draft.
5. Confirm the public Release's tag resolves to the recorded source commit and
   all five expected assets are present. GitHub also supplies source `.zip` and
   `.tar.gz` links, which are not entries in `SHA256SUMS`.

This two-commit request exists because a contents connector can commit files but
cannot dispatch workflows or upload binary assets. It requires no PAT, native
`gh` login, or persistent credential. Only the publish job has `contents: write`;
API authorization uses its ephemeral `GITHUB_TOKEN`. No release job runs for a
pull-request event. The tag created with `GITHUB_TOKEN` does not recursively
trigger a second tag-push workflow.

## Future releases and retries

For a future release, push an exact numeric tag such as `0.2.0` at the reviewed
source commit. Alternatively, use Actions → Release → Run workflow and enter an
**existing** exact numeric tag. Manual dispatch never creates a missing tag.
Tags with `v` prefixes, prerelease suffixes, path separators, or shell characters
are rejected. Annotated and lightweight tags are both resolved to a commit.
All release runs share a concurrency lock and do not cancel a prior release.

A rerun never moves or deletes a tag. It refuses a tag that points anywhere other
than the built commit, an unrelated existing Release body/identity, unexpected
assets, or any asset whose downloaded bytes differ from the newly built asset.
Existing identical assets are skipped after downloading and hashing them. An
upload or verification failure leaves a newly created Release in draft; rerun
only after diagnosing the failure. A successful rerun does not rewrite existing
Release notes or overwrite assets. If an asset is missing from an immutable
Release, the run fails rather than attempting to bypass immutability. If checksums
differ, investigate and publish a new version instead of deleting/replacing old
assets. Future changes to the release-note template intentionally require review
before retrying an older release.

## Checks and user verification

Before publishing, Linux/Python 3.12 runs Python unit and synthetic-video
acceptance tests. Every native build runs Go tests and vet, checks host OS/CPU
and exact Go version, compares two builds, validates archive members/modes and
BUILD-INFO, then executes `--help` on executable bytes re-read from the final
archive. **These are synthetic/software tests, not physical-camera validation.**
Real screen-to-camera performance depends on display, camera, focus, exposure,
frame rate, distance and ambient lighting; it must be measured separately.

Offline helper tests (no credentials required):

```sh
python -m unittest discover -s scripts -p 'test_release.py' -v
```

After downloading all four archives and `SHA256SUMS`, verify on Linux:

```sh
sha256sum -c SHA256SUMS
```

On macOS use `shasum -a 256 -c SHA256SUMS`. On Windows PowerShell, compare an
individual asset's `Get-FileHash .\txqr-transfer_0.1.0_windows_amd64.zip -Algorithm
SHA256` result with its line in `SHA256SUMS`. A checksum detects corruption; it is
not a cryptographic publisher signature. Download checksum files and assets only
from the intended repository Release.
