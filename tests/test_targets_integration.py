import tempfile
import unittest
from pathlib import Path

from coldstart.scanner import scan


class LocalTargetIntegrationTests(unittest.TestCase):
    def test_module_and_shell_references_use_real_report_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "scripts/chat_cli.py").write_text("raise RuntimeError('must never execute')")
            (root / "run.sh").write_text("exit 99")
            (root / "README.md").write_text(
                "```sh\npython -m scripts.chat_cli\n```\n"
                "```sh\nbash run.sh\n```\n"
                "```sh\npython -m installed_elsewhere\n```\n")
            report = scan(root)
            self.assertEqual(report["summary"], {"pass": 2, "fail": 0, "unknown": 1})
            self.assertEqual([c["line"] for c in report["checks"]], [2, 5, 8])
            self.assertTrue(all("unverified" in c["evidence"] for c in report["checks"][:2]))

    def test_module_does_not_make_later_runtime_state_known(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tool.py").write_text("")
            (root / "README.md").write_text("```sh\npython -m tool\npython generated.py\n```\n")
            report = scan(root)
            self.assertEqual(report["summary"], {"pass": 1, "fail": 0, "unknown": 1})
