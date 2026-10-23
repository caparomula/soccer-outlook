"""Retain only a referenced, verified core from isolated local Git trees."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("restore_outlook_runtime", ROOT / ".github/scripts/restore-outlook-runtime.py")
restore = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(restore)


def asset(content):
    return "outlook-core-" + hashlib.sha256(content).hexdigest() + ".js"


class OutlookAssetRetention(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.out = self.root / "site"
        self.git("init", "-q")

    def git(self, *args, data=None):
        return subprocess.run(["git", *args], cwd=self.repo, input=data,
                              capture_output=True, check=True, timeout=10).stdout

    def publication(self, files):
        # Object plumbing avoids checkout, staging, commits or user identity.
        records = []
        for name, data in sorted(files.items()):
            oid = self.git("hash-object", "-w", "--stdin", data=data).strip()
            records.append(b"100644 blob " + oid + b"\t" + name.encode() + b"\n")
        tree = self.git("mktree", data=b"".join(records)).decode().strip()
        (self.repo / ".git/FETCH_HEAD").write_text(tree + "\n")
        return "FETCH_HEAD"

    def test_restores_only_the_asset_referenced_by_the_previous_page(self):
        previous, historical, current = b"previous core", b"older unreferenced core", b"new core"
        name, old_name, new_name = asset(previous), asset(historical), asset(current)
        ref = self.publication({"index.html": f'<script integrity="unused" src="{name}"></script>'.encode(),
                                name: previous, old_name: historical})
        self.out.mkdir()
        (self.out / new_name).write_bytes(current)
        self.assertEqual(restore.restore(ref, self.out, self.repo), name)
        self.assertEqual((self.out / name).read_bytes(), previous)
        self.assertEqual((self.out / new_name).read_bytes(), current)
        self.assertEqual({path.name for path in self.out.iterdir()}, {name, new_name})
        self.assertEqual(restore.restore(ref, self.out, self.repo), name)

    def test_legacy_missing_index_or_inline_core_is_a_noop(self):
        for files in ({}, {"index.html": b"<html><script>window.oldInlineCore=true;</script></html>"}):
            with self.subTest(files=list(files)):
                ref = self.publication(files)
                self.assertIsNone(restore.restore(ref, self.out, self.repo))
                self.assertFalse(self.out.exists())

    def test_only_exact_script_src_basenames_are_candidates(self):
        name = asset(b"not copied")
        for markup in (f'<a href="{name}">link</a>', f'<!-- <script src="{name}"></script> -->',
                       f'<script src="../{name}"></script>', f'<script src="/{name}"></script>',
                       f'<script src="https://example.invalid/{name}"></script>',
                       f'<script src="{name}?version=1"></script>'):
            with self.subTest(markup=markup):
                ref = self.publication({"index.html": markup.encode(), name: b"not copied"})
                self.assertIsNone(restore.restore(ref, self.out, self.repo))
                self.assertFalse(self.out.exists())

    def test_missing_or_corrupt_referenced_asset_fails_without_writing(self):
        name = asset(b"expected core")
        for extra, message in (({}, "missing"), ({name: b"different bytes"}, "SHA-256")):
            with self.subTest(message=message):
                ref = self.publication({"index.html": f'<script src="{name}"></script>'.encode(), **extra})
                with self.assertRaisesRegex(restore.RetentionError, message):
                    restore.restore(ref, self.out, self.repo)
                self.assertFalse(self.out.exists())

    def test_index_and_core_limits_are_checked_before_reading_large_blobs(self):
        raw = b"x" * 65
        name = asset(raw)
        markup = f'<script src="{name}"></script>'.encode()
        ref = self.publication({"index.html": markup, name: raw})
        with patch.object(restore, "MAX_CORE_BYTES", 64), \
                patch.object(restore, "git_read", wraps=restore.git_read) as read:
            with self.assertRaisesRegex(restore.RetentionError, "byte limit"):
                restore.restore(ref, self.out, self.repo)
            content_reads = [call for call in read.call_args_list if call.args[1:3] == ("cat-file", "blob")]
            self.assertEqual(len(content_reads), 1, "only the bounded index may be buffered")
        with patch.object(restore, "MAX_INDEX_BYTES", len(markup) - 1), \
                patch.object(restore, "git_read", wraps=restore.git_read) as read:
            with self.assertRaisesRegex(restore.RetentionError, "byte limit"):
                restore.restore(ref, self.out, self.repo)
            self.assertFalse(any(call.args[1:3] == ("cat-file", "blob") for call in read.call_args_list))
        self.assertFalse(self.out.exists())

    def test_ambiguous_distinct_cores_and_duplicate_src_are_rejected(self):
        first, second = asset(b"first"), asset(b"second")
        for markup in (f'<script src="{first}"></script><script src="{second}"></script>',
                       f'<script src="{first}" src="{second}"></script>'):
            with self.subTest(markup=markup):
                ref = self.publication({"index.html": markup.encode(), first: b"first", second: b"second"})
                with self.assertRaises(restore.RetentionError):
                    restore.restore(ref, self.out, self.repo)
                self.assertFalse(self.out.exists())

    def test_invalid_local_ref_is_not_mistaken_for_a_legacy_page(self):
        with self.assertRaisesRegex(restore.RetentionError, "local publication"):
            restore.restore("does-not-exist", self.out, self.repo)
        self.assertFalse(self.out.exists())

    def test_cli_uses_fetch_head_and_reports_missing_asset_as_failure(self):
        name = asset(b"missing")
        self.publication({"index.html": f'<script src="{name}"></script>'.encode()})
        with patch.object(restore.Path, "cwd", return_value=self.repo), \
                redirect_stderr(io.StringIO()) as errors, redirect_stdout(io.StringIO()):
            self.assertEqual(restore.main(["--out", str(self.out)]), 1)
        self.assertIn("missing core asset", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
