"""Offline tests: no GitHub credentials or remote mutations required."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.request
import zipfile

import release

TAG = "0.1.0"
COMMIT = "a" * 40


def make_dist(path):
    for target in release.TARGETS:
        files = {name: (b"example\n", 0o644) for name in release.DOCS}
        files["BUILD-INFO"] = (release.build_info(TAG, COMMIT, target), 0o644)
        files["txqr-go.exe" if target[0] == "windows" else "txqr-go"] = (b"example executable", 0o755)
        data = release.archive_bytes(files, target[0] == "windows")
        name = release.asset_name(TAG, target)
        (path / name).write_bytes(data)
        (path / (name + ".sha256")).write_text(release.digest(data) + "\n", encoding="ascii")


class FakeAPI:
    def __init__(self):
        self.commit = None
        self.release = None
        self.assets = {}
        self.mutations = []
        self.corrupt_download = False

    def tag_commit(self, tag):
        return self.commit

    def request(self, method, path, value=None, binary=False, missing=False):
        if method != "GET":
            self.mutations.append((method, path))
        if path == "/git/refs":
            self.commit = value["sha"]
            return {}
        if path.startswith("/releases/tags/"):
            return None if self.release and self.release["draft"] else self.release
        if path.startswith("/releases?per_page=100&page="):
            return [self.release] if self.release else []
        if path == "/releases" and method == "POST":
            self.release = dict(value, id=1)
            return self.release
        if path == "/releases/1/assets?per_page=100":
            return [{"id": i, "name": name} for i, (name, _) in self.assets.items()]
        if path.startswith("https://uploads.github.com/"):
            name = release.urllib.parse.parse_qs(release.urllib.parse.urlsplit(path).query)["name"][0]
            i = len(self.assets) + 1
            self.assets[i] = (name, value)
            return {"id": i, "name": name}
        if path.startswith("/releases/assets/"):
            data = self.assets[int(path.rsplit("/", 1)[1])][1]
            return data + b"corrupt" if self.corrupt_download else data
        if path == "/releases/1" and method == "PATCH":
            self.release.update(value)
            return self.release
        raise AssertionError((method, path))


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dist = Path(self.temp.name)
        make_dist(self.dist)

    def test_untracked_go_source_rejected_but_ignored_cache_is_allowed(self):
        source = self.dist / "source"
        source.mkdir()
        (source / ".gitignore").write_text("build/\n")
        release.subprocess.run(["git", "init", "-q"], cwd=source, check=True)
        release.subprocess.run(["git", "add", ".gitignore"], cwd=source, check=True)
        release.subprocess.run(["git", "-c", "user.name=Release test", "-c", "user.email=release-test@example.invalid", "commit", "-qm", "fixture"], cwd=source, check=True)
        commit = release.run(["git", "rev-parse", "HEAD"], cwd=source)
        (source / "build").mkdir()
        (source / "build" / "cache").write_text("ignored cache")
        self.assertEqual(release.run(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=source), "")
        (source / "sender-go").mkdir()
        (source / "sender-go" / "injected.go").write_text("package main\n")
        with self.assertRaisesRegex(ValueError, "untracked additions"):
            release.build(source, self.dist / "out", TAG, commit, ("linux", "amd64"))

    def test_untracked_additions_from_tests_rejected_before_build(self):
        with patch.object(release, "run", side_effect=[COMMIT, "", release.GO_VERSION, "linux", "amd64", "?? sender-go/injected.go"]), patch.object(release.subprocess, "run") as commands:
            with self.assertRaisesRegex(ValueError, "tests modified source"):
                release.build(self.dist, self.dist / "out", TAG, COMMIT, ("linux", "amd64"))
        self.assertEqual(commands.call_count, 2)  # go test and vet, never go build

    def test_identity_rejects_injection_and_partial_commits(self):
        for tag in ("v0.1.0", "0.1.0;echo pwn", "../0.1.0", "01.1.0", "0.1.0\nextra", "0.1.0-beta"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release.identity(tag, COMMIT)
        for commit in ("main", "abc123", "A" * 40, "a" * 40 + "\n"):
            with self.subTest(commit=commit), self.assertRaises(ValueError):
                release.identity(TAG, commit)

    def test_archives_all_targets_and_checksums(self):
        files = release.collect(self.dist, TAG, COMMIT)
        self.assertEqual(len(files), 5)
        self.assertEqual(len(files["SHA256SUMS"].splitlines()), 4)
        for target in release.TARGETS:
            path = self.dist / release.asset_name(TAG, target)
            content = release.inspect_archive(path, TAG, COMMIT, target)
            self.assertEqual(content["BUILD-INFO"], release.build_info(TAG, COMMIT, target))

    def test_archive_is_reproducible_with_fixed_metadata(self):
        files = {"txqr-go": (b"bytes", 0o755), "LICENSE": (b"license", 0o644)}
        for windows in (False, True):
            self.assertEqual(release.archive_bytes(files, windows), release.archive_bytes(dict(reversed(list(files.items()))), windows))

    def test_archive_duplicate_traversal_and_modes_rejected(self):
        target = ("windows", "amd64")
        path = self.dist / release.asset_name(TAG, target)
        for bad_name, bad_mode in (("../txqr-go.exe", 0o755), ("txqr-go.exe", 0o644)):
            files = {name: (b"doc", 0o644) for name in release.DOCS}
            files["BUILD-INFO"] = (release.build_info(TAG, COMMIT, target), 0o644)
            files[bad_name] = (b"exe", bad_mode)
            path.write_bytes(release.archive_bytes(files, True))
            with self.assertRaises(ValueError):
                release.inspect_archive(path, TAG, COMMIT, target)
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as archive:
            archive.writestr("txqr-go.exe", b"first")
            archive.writestr("txqr-go.exe", b"second")
        path.write_bytes(content.getvalue())
        with self.assertRaises(ValueError):
            release.inspect_archive(path, TAG, COMMIT, target)

    def test_tampered_build_info_and_digest_rejected(self):
        with self.assertRaises(ValueError):
            release.collect(self.dist, TAG, "b" * 40)
        digest_file = next(self.dist.glob("*.sha256"))
        digest_file.write_text("0" * 64 + "\n")
        with self.assertRaises(ValueError):
            release.collect(self.dist, TAG, COMMIT)

    def test_publish_and_identical_rerun(self):
        api = FakeAPI()
        release.publish(api, self.dist, TAG, COMMIT, True)
        self.assertFalse(api.release["draft"])
        self.assertEqual(api.commit, COMMIT)
        self.assertEqual(len(api.assets), 5)
        api.mutations.clear()
        release.publish(api, self.dist, TAG, COMMIT, True)
        self.assertEqual(api.mutations, [])

    def test_mismatched_tag_never_mutates(self):
        api = FakeAPI()
        api.commit = "b" * 40
        with self.assertRaisesRegex(ValueError, "tag differs"):
            release.publish(api, self.dist, TAG, COMMIT, True)
        self.assertEqual(api.mutations, [])

    def test_missing_tag_requires_one_time_authority(self):
        api = FakeAPI()
        with self.assertRaisesRegex(ValueError, "tag is missing"):
            release.publish(api, self.dist, TAG, COMMIT, False)
        self.assertEqual(api.mutations, [])
        with self.assertRaisesRegex(ValueError, "only first release"):
            release.publish(api, self.dist, "0.2.0", COMMIT, True)

    def test_mismatched_existing_asset_never_overwrites(self):
        api = FakeAPI()
        release.publish(api, self.dist, TAG, COMMIT, True)
        name, _ = api.assets[1]
        api.assets[1] = (name, b"different bytes")
        api.mutations.clear()
        with self.assertRaisesRegex(ValueError, "existing asset differs"):
            release.publish(api, self.dist, TAG, COMMIT, True)
        self.assertEqual(api.mutations, [])

    def test_existing_unrelated_release_is_not_changed(self):
        api = FakeAPI()
        api.commit = COMMIT
        api.release = {"id": 1, "tag_name": TAG, "body": "someone else's release", "draft": False, "prerelease": False}
        with self.assertRaisesRegex(ValueError, "identity/notes differ"):
            release.publish(api, self.dist, TAG, COMMIT, True)
        self.assertEqual(api.mutations, [])

    def test_postupload_corruption_keeps_release_draft(self):
        api = FakeAPI()
        api.corrupt_download = True
        with self.assertRaisesRegex(ValueError, "downloaded asset checksum mismatch"):
            release.publish(api, self.dist, TAG, COMMIT, True)
        self.assertTrue(api.release["draft"])
        self.assertNotIn(("PATCH", "/releases/1"), api.mutations)

    def test_interrupted_draft_upload_resumes_without_duplicate_release(self):
        api = FakeAPI()
        real_request = api.request
        uploads = 0
        def interrupted(method, path, value=None, **kwargs):
            nonlocal uploads
            if method == "POST" and path.startswith("https://uploads.github.com/"):
                uploads += 1
                if uploads == 3:
                    raise RuntimeError("interrupted upload")
            return real_request(method, path, value, **kwargs)
        with patch.object(api, "request", side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, "interrupted upload"):
                release.publish(api, self.dist, TAG, COMMIT, True)
        self.assertTrue(api.release["draft"])
        self.assertEqual(len(api.assets), 2)
        self.assertIsNone(api.request("GET", "/releases/tags/" + TAG, missing=True))
        api.mutations.clear()
        release.publish(api, self.dist, TAG, COMMIT, True)
        self.assertFalse(api.release["draft"])
        self.assertEqual(len(api.assets), 5)
        self.assertNotIn(("POST", "/releases"), api.mutations)

    def test_duplicate_drafts_and_inconclusive_pagination_rejected(self):
        api = FakeAPI()
        with patch.object(api, "request", side_effect=[None, [{"id": 1, "tag_name": TAG}, {"id": 2, "tag_name": TAG}]]):
            with self.assertRaisesRegex(ValueError, "multiple releases"):
                release.find_release(api, TAG)
        with patch.object(api, "request", side_effect=[None] + [[{"id": n, "tag_name": "9.9.9"} for n in range(100)]] * 10):
            with self.assertRaisesRegex(ValueError, "pagination limit"):
                release.find_release(api, TAG)

    def test_request_exact_schema_and_ancestry(self):
        path = self.dist / "request.json"
        api = FakeAPI()
        path.write_text(json.dumps({"tag": TAG, "commit": COMMIT}))
        with patch.object(release.subprocess, "run") as command:
            result = release.resolve(api, "push", "refs/heads/main", "b" * 40, "", path)
        self.assertEqual(result["commit"], COMMIT)
        self.assertEqual(result["create_tag"], "true")
        command.assert_called_once_with(["git", "merge-base", "--is-ancestor", COMMIT, "b" * 40], check=True)
        path.write_text(json.dumps({"tag": "0.2.0", "commit": COMMIT}))
        with self.assertRaises(ValueError):
            release.resolve(api, "push", "refs/heads/main", "b" * 40, "", path)

    def test_manual_requires_existing_tag_and_pr_rejected(self):
        api = FakeAPI()
        with self.assertRaisesRegex(ValueError, "existing tag"):
            release.resolve(api, "workflow_dispatch", "refs/heads/main", COMMIT, TAG, "unused")
        with self.assertRaisesRegex(ValueError, "unsupported release event"):
            release.resolve(api, "pull_request", "refs/pull/1/merge", COMMIT, TAG, "unused")

    def test_tag_peeling_lightweight_and_annotated(self):
        api = object.__new__(release.API)
        with patch.object(api, "request", return_value={"object": {"type": "commit", "sha": COMMIT}}):
            self.assertEqual(api.tag_commit(TAG), COMMIT)
        with patch.object(api, "request", side_effect=[{"object": {"type": "tag", "sha": "b" * 40}}, {"object": {"type": "commit", "sha": COMMIT}}]):
            self.assertEqual(api.tag_commit(TAG), COMMIT)

    def test_redirect_does_not_leak_token_and_rejects_untrusted_hosts(self):
        request = urllib.request.Request("https://api.github.com/repos/example/assets/1", headers={"Authorization": "Bearer secret"})
        handler = release.SafeRedirect()
        redirected = handler.redirect_request(request, None, 302, "found", {}, "https://release-assets.githubusercontent.com/file")
        self.assertFalse(redirected.has_header("Authorization"))
        for bad in ("http://release-assets.githubusercontent.com/file", "https://evil.example/file", "https://u@release-assets.githubusercontent.com/file"):
            with self.subTest(url=bad), self.assertRaises(ValueError):
                handler.redirect_request(request, None, 302, "found", {}, bad)


if __name__ == "__main__":
    unittest.main()
