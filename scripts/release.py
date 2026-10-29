#!/usr/bin/env python3
"""Small, dependency-free release tooling. Never retags or overwrites an asset."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

GO_VERSION = "go1.27.3"
TARGETS = (("linux", "amd64"), ("linux", "arm64"), ("darwin", "arm64"), ("windows", "amd64"))
DOCS = ("LICENSE", "README.md", "THIRD_PARTY.md")
REPOSITORY = "shrekee/txqr-transfer"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identity(tag, commit):
    require(bool(re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", tag)), "tag must be exact numeric semver (no v prefix)")
    require(bool(re.fullmatch(r"[0-9a-f]{40}", commit)), "expected full lowercase source commit")


def asset_name(tag, target):
    require(target in TARGETS, "unsupported target")
    return f"txqr-transfer_{tag}_{target[0]}_{target[1]}" + (".zip" if target[0] == "windows" else ".tar.gz")


def build_info(tag, commit, target):
    identity(tag, commit)
    return f"tag={tag}\ncommit={commit}\ntarget={target[0]}/{target[1]}\ngo={GO_VERSION}\nCGO_ENABLED=0\n".encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def archive_bytes(files, windows=False):
    """Canonical root-only archives: fixed timestamps/ownership/order/modes."""
    result = io.BytesIO()
    if windows:
        with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, (data, mode) in sorted(files.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | mode) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data, compresslevel=9)
    else:
        with gzip.GzipFile(filename="", fileobj=result, mode="wb", mtime=0, compresslevel=9) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                for name, (data, mode) in sorted(files.items()):
                    info = tarfile.TarInfo(name)
                    info.size, info.mode = len(data), mode
                    info.uid = info.gid = info.mtime = 0
                    archive.addfile(info, io.BytesIO(data))
    return result.getvalue()


def inspect_archive(path, tag, commit, target):
    executable = "txqr-go.exe" if target[0] == "windows" else "txqr-go"
    expected = set(DOCS) | {"BUILD-INFO", executable}
    files = {}
    if target[0] == "windows":
        with zipfile.ZipFile(path) as archive:
            require(len(archive.infolist()) == len(expected), "unexpected/duplicate archive entries")
            for info in archive.infolist():
                mode = info.external_attr >> 16
                require(stat.S_ISREG(mode), "archive entry is not a regular file")
                require(stat.S_IMODE(mode) == (0o755 if info.filename == executable else 0o644), "incorrect archive mode")
                files[info.filename] = archive.read(info)
    else:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            require(len(members) == len(expected), "unexpected/duplicate archive entries")
            for info in members:
                require(info.isfile(), "archive entry is not a regular file")
                require(info.mode == (0o755 if info.name == executable else 0o644), "incorrect archive mode")
                files[info.name] = archive.extractfile(info).read()
    require(set(files) == expected, "archive member names differ")
    require(files["BUILD-INFO"] == build_info(tag, commit, target), "BUILD-INFO mismatch")
    require(bool(files[executable]), "empty executable")
    return files


def run(args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def build(source, dist, tag, commit, target):
    identity(tag, commit)
    source, dist = Path(source).resolve(), Path(dist).resolve()
    require(run(["git", "rev-parse", "HEAD"], cwd=source) == commit, "checkout is not exact source commit")
    require(not run(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=source), "source has tracked modifications or untracked additions")
    require(run(["go", "env", "GOVERSION"]) == GO_VERSION, "unexpected Go version")
    require((run(["go", "env", "GOHOSTOS"]), run(["go", "env", "GOHOSTARCH"])) == target, "build must execute on matching native runner")
    env = dict(os.environ, CGO_ENABLED="0", GOOS=target[0], GOARCH=target[1], GOTOOLCHAIN="local")
    module = source / "sender-go"
    subprocess.run(["go", "test", "-mod=readonly", "-count=1", "./..."], cwd=module, env=env, check=True)
    subprocess.run(["go", "vet", "-mod=readonly", "./..."], cwd=module, env=env, check=True)
    require(not run(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=source), "tests modified source or created untracked additions")
    dist.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="txqr-build-", dir=dist.parent) as temp:
        executable = "txqr-go.exe" if target[0] == "windows" else "txqr-go"
        binary = Path(temp) / executable
        command = ["go", "build", "-mod=readonly", "-trimpath", "-buildvcs=false", "-o", str(binary), "."]
        subprocess.run(command, cwd=module, env=env, check=True)
        first = binary.read_bytes()
        subprocess.run(command, cwd=module, env=env, check=True)
        require(first == binary.read_bytes(), "same-runner Go rebuild was not reproducible")
        require(not run(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=source), "build modified source or created untracked additions")
        files = {name: ((source / name).read_bytes(), 0o644) for name in DOCS}
        files["BUILD-INFO"] = (build_info(tag, commit, target), 0o644)
        files[executable] = (first, 0o755)
        output = dist / asset_name(tag, target)
        data = archive_bytes(files, target[0] == "windows")
        require(data == archive_bytes(files, target[0] == "windows"), "archive not reproducible")
        output.write_bytes(data)
        packed = inspect_archive(output, tag, commit, target)
        require(packed == {name: value[0] for name, value in files.items()}, "archive content changed")
        # Smoke-test bytes re-read from the final archive, not a different binary.
        binary.write_bytes(packed[executable])
        binary.chmod(0o755)
        subprocess.run([str(binary), "--help"], check=True)
        (dist / (output.name + ".sha256")).write_text(digest(data) + "\n", encoding="ascii")
        print(f"{digest(data)}  {output.name}")


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        require(target.scheme == "https" and target.hostname and not target.username,
                "unsafe download redirect")
        origin = urllib.parse.urlsplit(request.full_url).hostname
        require(target.hostname == origin or target.hostname.endswith(".githubusercontent.com"),
                "unexpected asset redirect host")
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)
        if target.hostname != origin:
            redirected.remove_header("Authorization")
        return redirected


class API:
    def __init__(self):
        require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "unexpected repository")
        self.token = os.environ["GH_TOKEN"]
        self.base = f"https://api.github.com/repos/{REPOSITORY}"

    def request(self, method, path, value=None, binary=False, missing=False):
        url = path if path.startswith("https://") else self.base + path
        parsed = urllib.parse.urlsplit(url)
        require(parsed.scheme == "https" and parsed.hostname in ("api.github.com", "uploads.github.com"), "untrusted API host")
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/octet-stream" if binary else "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "txqr-transfer-release"}
        body = None
        if value is not None:
            if isinstance(value, bytes):
                body, headers["Content-Type"] = value, "application/octet-stream"
            else:
                body, headers["Content-Type"] = json.dumps(value).encode(), "application/json"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.build_opener(SafeRedirect()).open(request, timeout=120) as response:
                data = response.read()
        except urllib.error.HTTPError as error:
            if missing and error.code == 404:
                return None
            raise
        return data if binary else (json.loads(data) if data else None)

    def tag_commit(self, tag):
        ref = self.request("GET", "/git/ref/tags/" + tag, missing=True)
        if ref is None:
            return None
        obj = ref["object"]
        for _ in range(8):
            if obj["type"] == "commit":
                identity(tag, obj["sha"])
                return obj["sha"]
            require(obj["type"] == "tag", "tag does not resolve to commit")
            obj = self.request("GET", "/git/tags/" + obj["sha"])["object"]
        raise ValueError("tag nesting is too deep")


def resolve(api, event, ref, sha, requested_tag, request_path):
    identity("0.1.0", sha)
    create_tag = False
    if event == "push" and ref == "refs/heads/main":
        request = json.loads(Path(request_path).read_text(encoding="utf-8"))
        require(set(request) == {"tag", "commit"} and request["tag"] == "0.1.0", "invalid one-time release request")
        tag, commit, create_tag = request["tag"], request["commit"], True
        identity(tag, commit)
        subprocess.run(["git", "merge-base", "--is-ancestor", commit, sha], check=True)
    elif event == "push" and ref.startswith("refs/tags/"):
        tag = ref.removeprefix("refs/tags/")
        identity(tag, sha)
        commit = api.tag_commit(tag)
        require(commit == run(["git", "rev-parse", "HEAD"]), "pushed tag changed")
    elif event == "workflow_dispatch":
        tag = requested_tag
        identity(tag, sha)
        commit = api.tag_commit(tag)
        require(commit is not None, "manual runs require an existing tag")
    else:
        raise ValueError("unsupported release event")
    identity(tag, commit)
    existing = api.tag_commit(tag)
    require(existing is None or existing == commit, "existing tag has a different source commit")
    return {"tag": tag, "commit": commit, "tooling": sha, "create_tag": str(create_tag).lower()}


def release_body(tag, commit):
    return f"""TXQR Transfer {tag}

Source commit: `{commit}`. Built with Go 1.27.3, CGO disabled.

### Download and usage
Choose the archive matching your OS/CPU, verify it against `SHA256SUMS`, then extract it. Each includes `txqr-go` (`txqr-go.exe` on Windows), README, MIT license, third-party notice, and BUILD-INFO.
Run `./txqr-go --help` (Windows: `.\\txqr-go.exe --help`) and follow the README sender/receiver examples.

### Compatibility and validation
Native Go unit tests, vet, repeat-build byte comparison, archive validation, and the packaged executable's `--help` run on Linux amd64, Linux arm64, macOS arm64, and Windows amd64 GitHub-hosted runners. This is not a claim that every supported OS version or physical camera has been tested. macOS binaries are unsigned and not notarized; Windows binaries are unsigned.
The Python decoder is **source**, not a standalone executable. Download this tag's GitHub source archive and use the pinned Python installation instructions in README. Synthetic video acceptance tests run on Linux/Python 3.12; real screen-to-camera transfer has not been physically validated.

See [README](https://github.com/{REPOSITORY}/blob/{commit}/README.md), [protocol](https://github.com/{REPOSITORY}/tree/{commit}/protocol), and [release process](https://github.com/{REPOSITORY}/blob/{commit}/docs/releases.md).
"""


def collect(dist, tag, commit):
    dist = Path(dist)
    result = {}
    for target in TARGETS:
        name = asset_name(tag, target)
        path = dist / name
        inspect_archive(path, tag, commit, target)
        data = path.read_bytes()
        require((dist / (name + ".sha256")).read_text(encoding="ascii") == digest(data) + "\n", "artifact digest mismatch")
        result[name] = data
    result["SHA256SUMS"] = "".join(f"{digest(data)}  {name}\n" for name, data in sorted(result.items())).encode()
    return result


def find_release(api, tag):
    """Tag lookup hides drafts; list with push permission to safely resume them."""
    published = api.request("GET", "/releases/tags/" + tag, missing=True)
    matches = {} if published is None else {published["id"]: published}
    for page in range(1, 11):
        releases = api.request("GET", f"/releases?per_page=100&page={page}")
        for item in releases:
            if item["tag_name"] == tag:
                matches[item["id"]] = item
        if len(releases) < 100:
            break
    else:
        raise ValueError("release pagination limit reached; refusing ambiguous creation")
    require(len(matches) <= 1, "multiple releases exist for tag; refusing mutation")
    return next(iter(matches.values()), None)


def publish(api, dist, tag, commit, create_tag):
    identity(tag, commit)
    require(not create_tag or tag == "0.1.0", "only first release request may create a tag")
    files = collect(dist, tag, commit)  # Complete validation before any mutation.
    current = api.tag_commit(tag)
    if current is None:
        require(create_tag, "expected tag is missing")
        api.request("POST", "/git/refs", {"ref": "refs/tags/" + tag, "sha": commit})
    require(api.tag_commit(tag) == commit, "tag differs from built source; refusing publication")
    body = release_body(tag, commit)
    release = find_release(api, tag)
    if release is None:
        release = api.request("POST", "/releases", {"tag_name": tag, "target_commitish": commit, "name": tag, "body": body, "draft": True, "prerelease": False})
    require(release["tag_name"] == tag and release["body"] == body and not release["prerelease"], "existing release identity/notes differ; refusing mutation")
    release_id = release["id"]
    assets = api.request("GET", f"/releases/{release_id}/assets?per_page=100")
    require(len(assets) < 100, "too many release assets")
    by_name = {asset["name"]: asset for asset in assets}
    require(len(by_name) == len(assets), "duplicate release asset names")
    require(set(by_name) <= set(files), "unexpected existing release assets")
    # Preflight all existing assets before uploading anything; never --clobber.
    for name, asset in by_name.items():
        actual = api.request("GET", f"/releases/assets/{asset['id']}", binary=True)
        require(digest(actual) == digest(files[name]), f"existing asset differs: {name}")
    for name, data in files.items():
        if name not in by_name:
            require(not release.get("immutable", False), "immutable release is missing an asset")
            url = f"https://uploads.github.com/repos/{REPOSITORY}/releases/{release_id}/assets?" + urllib.parse.urlencode({"name": name})
            asset = api.request("POST", url, data)
            by_name[name] = asset
    # Independently fetch all uploaded bytes. Checksums include all four archives.
    downloaded = {}
    for name in files:
        actual = api.request("GET", f"/releases/assets/{by_name[name]['id']}", binary=True)
        require(digest(actual) == digest(files[name]), f"downloaded asset checksum mismatch: {name}")
        downloaded[name] = actual
    for line in downloaded["SHA256SUMS"].decode("ascii").splitlines():
        checksum, name = line.split("  ", 1)
        require(digest(downloaded[name]) == checksum, "uploaded SHA256SUMS verification failed")
    require(api.tag_commit(tag) == commit, "tag changed before publication")
    if release["draft"]:
        api.request("PATCH", f"/releases/{release_id}", {"draft": False})
    final = api.request("GET", "/releases/tags/" + tag)
    require(final["id"] == release_id and not final["draft"], "release did not publish")
    print(f"Verified release https://github.com/{REPOSITORY}/releases/tag/{tag} at {commit}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("resolve", "build", "publish"))
    parser.add_argument("--source", default="source")
    parser.add_argument("--dist", default="dist")
    parser.add_argument("--tag", default=os.environ.get("TAG", ""))
    parser.add_argument("--commit", default=os.environ.get("COMMIT", ""))
    parser.add_argument("--os", dest="target_os", default=os.environ.get("TARGET_OS", ""))
    parser.add_argument("--arch", default=os.environ.get("TARGET_ARCH", ""))
    args = parser.parse_args()
    if args.command == "build":
        build(args.source, args.dist, args.tag, args.commit, (args.target_os, args.arch))
    elif args.command == "publish":
        publish(API(), args.dist, args.tag, args.commit, os.environ.get("CREATE_TAG") == "true")
    else:
        outputs = resolve(API(), os.environ["GITHUB_EVENT_NAME"], os.environ["GITHUB_REF"], os.environ["GITHUB_SHA"], os.environ.get("REQUESTED_TAG", ""), ".github/release-requests/0.1.0.json")
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            for key, value in outputs.items():
                output.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
