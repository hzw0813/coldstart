"""Offline integrity tests for the pinned corpus collector."""

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "benchmarks" / "run.py"
SPEC = importlib.util.spec_from_file_location("coldstart_benchmark", MODULE_PATH)
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.checkouts = self.base / "checkouts"
        self.repo = self.checkouts / "sample"
        self.repo.mkdir(parents=True)
        self.git("init", "--quiet")
        (self.repo / "README.md").write_text("```sh\nnpm run absent\n```\n", encoding="utf-8")
        (self.repo / "package.json").write_text('{"scripts":{"test":"echo fixture"}}\n')
        self.git("add", "README.md", "package.json")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
        self.commit = self.git("rev-parse", "HEAD").strip()
        self.sample = {"id": "sample", "repository": "example/public-project", "commit": self.commit,
                       "readme": "README.md", "readme_sha256": hashlib.sha256((self.repo / "README.md").read_bytes()).hexdigest(),
                       "license": "MIT", "sample_note": "Local synthetic collector test; no network."}
        self.manifest = self.base / "corpus.json"
        self.output = self.base / "reports"
        self.write_manifest()

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        result = subprocess.run(["git", "-c", f"core.hooksPath={os.devnull}", "-C", str(self.repo), *args],
                                capture_output=True, text=True, check=True, env=benchmark._git_environment())
        return result.stdout

    def write_manifest(self, rows=None):
        self.manifest.write_text(json.dumps(rows if rows is not None else [self.sample]))

    def collect(self, **kwargs):
        return benchmark.collect(self.manifest, self.checkouts, self.output, **kwargs)

    def test_collects_broken_findings_without_changing_checkout(self):
        before = self.git("status", "--porcelain=v1")
        summary = self.collect()
        self.assertEqual(summary[0]["summary"]["fail"], 1)
        self.assertEqual(summary[0]["commit"], self.commit)
        self.assertIn(self.commit, summary[0]["source_url"])
        self.assertFalse(summary[0]["commands_executed"])
        self.assertEqual(summary[0]["scanner"], benchmark.scanner_identity())
        self.assertEqual(self.git("status", "--porcelain=v1"), before)
        for suffix in ("json", "html"):
            text = (self.output / f"sample.{suffix}").read_text()
            self.assertNotIn(str(self.base), text)
            self.assertIn("not endorsement", text)
        self.assertEqual(json.loads((self.output / "summary.json").read_text()), summary)

    def test_wrong_commit_rejected_even_with_fetch(self):
        self.sample["commit"] = "0" * 40
        self.write_manifest()
        with self.assertRaisesRegex(benchmark.BenchmarkError, "HEAD differs"):
            self.collect(fetch=True)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.commit)
        self.assertFalse(self.output.exists())

    def test_tracked_change_rejected_and_preserved(self):
        marker = "modified local README\n"
        (self.repo / "README.md").write_text(marker)
        with self.assertRaisesRegex(benchmark.BenchmarkError, "changes"):
            self.collect()
        self.assertEqual((self.repo / "README.md").read_text(), marker)

    def test_untracked_and_ignored_content_is_rejected(self):
        (self.repo / "untracked.txt").write_text("local content")
        with self.assertRaisesRegex(benchmark.BenchmarkError, "changes"):
            self.collect()
        (self.repo / "untracked.txt").unlink()
        (self.repo / ".git" / "info" / "exclude").write_text("ignored.txt\n")
        (self.repo / "ignored.txt").write_text("hidden local content")
        with self.assertRaisesRegex(benchmark.BenchmarkError, "changes"):
            self.collect()

    def test_readme_hash_mismatch_rejected(self):
        self.sample["readme_sha256"] = "0" * 64
        self.write_manifest()
        with self.assertRaisesRegex(benchmark.BenchmarkError, "hash differs"):
            self.collect()
        self.assertFalse(self.output.exists())

    def test_manifest_rejects_traversal_urls_duplicates_and_invalid_values(self):
        mutations = [
            {"id": "../sample"}, {"repository": "https://github.com/example/repo"},
            {"repository": "example/.."}, {"commit": "HEAD"},
            {"readme": "../README.md"}, {"readme": "/README.md"},
            {"readme": "a/../README.md"}, {"readme": "a\\README.md"},
            {"readme_sha256": "abc"}, {"sample_note": 123},
        ]
        for changes in mutations:
            with self.subTest(changes=changes):
                self.write_manifest([{**self.sample, **changes}])
                with self.assertRaises(benchmark.BenchmarkError):
                    self.collect()
        for rows in ([], {}, [self.sample, self.sample], [{**self.sample, "unexpected": "field"}]):
            self.write_manifest(rows)
            with self.assertRaises(benchmark.BenchmarkError):
                self.collect()

    def test_readme_symlink_cannot_escape_checkout(self):
        external = self.base / "outside.md"
        external.write_text("outside data")
        readme = self.repo / "README.md"
        readme.unlink()
        readme.symlink_to(external)
        self.git("add", "README.md")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "symlink fixture")
        self.sample["commit"] = self.git("rev-parse", "HEAD").strip()
        self.sample["readme_sha256"] = hashlib.sha256(external.read_bytes()).hexdigest()
        self.write_manifest()
        with self.assertRaisesRegex(benchmark.BenchmarkError, "within the checkout"):
            self.collect()

    def test_missing_checkout_does_not_fetch_by_default(self):
        self.sample["id"] = "missing"
        self.write_manifest()
        with self.assertRaisesRegex(benchmark.BenchmarkError, "checkout is missing"):
            self.collect()
        self.assertFalse((self.checkouts / "missing").exists())

    def test_output_cannot_modify_upstream_checkout(self):
        self.output = self.repo / "reports"
        with self.assertRaisesRegex(benchmark.BenchmarkError, "inside an upstream"):
            self.collect()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.git("status", "--porcelain=v1"), "")

    def test_output_symlink_is_not_followed(self):
        outside = self.base / "keep.txt"
        outside.write_text("keep me")
        self.output.mkdir()
        (self.output / "sample.json").symlink_to(outside)
        with self.assertRaisesRegex(benchmark.BenchmarkError, "symlink"):
            self.collect()
        self.assertEqual(outside.read_text(), "keep me")

    def test_local_git_filters_do_not_execute_project_commands(self):
        (self.repo / ".gitattributes").write_text("README.md filter=fixture\n")
        self.git("add", ".gitattributes")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "attributes")
        self.sample["commit"] = self.git("rev-parse", "HEAD").strip()
        self.write_manifest()
        marker = self.base / "FILTER_MUST_NOT_RUN"
        self.git("config", "filter.fixture.clean", f"touch {marker}")
        self.collect()
        self.assertFalse(marker.exists())

    def test_fetch_requires_unauthenticated_public_repository_metadata(self):
        for metadata in ({"private": True, "full_name": "example/public-project"},
                         {"private": False, "full_name": "different/repository"}, {}):
            with self.subTest(metadata=metadata):
                with mock.patch.object(benchmark, "urlopen", return_value=io.BytesIO(json.dumps(metadata).encode())):
                    with self.assertRaises(benchmark.BenchmarkError):
                        benchmark.verify_public_repository("example/public-project")
        metadata = {"private": False, "full_name": "example/public-project"}
        with mock.patch.object(benchmark, "urlopen", return_value=io.BytesIO(json.dumps(metadata).encode())) as opened:
            benchmark.verify_public_repository("example/public-project")
            request = opened.call_args.args[0]
            self.assertNotIn("Authorization", dict(request.header_items()))


if __name__ == "__main__":
    unittest.main()
