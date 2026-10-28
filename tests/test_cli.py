import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from coldstart.cli import terminal_safe


PROJECT = Path(__file__).resolve().parents[1]


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "coldstart", *map(str, args)],
                              cwd=PROJECT, capture_output=True, text=True, timeout=10)

    def test_broken_demo_has_three_specific_failures(self):
        result = self.run_cli("examples/broken", "--format", "json")
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["summary"]["fail"], 3)
        commands = {item["command"] for item in report["checks"] if item["status"] == "fail"}
        self.assertTrue(any("npm run dev" in command for command in commands))
        self.assertTrue(any("requirements-old.txt" in command for command in commands))
        self.assertTrue(any("scripts/build.py" in command for command in commands))

    def test_fixed_demo_still_reports_runtime_uncertainty(self):
        result = self.run_cli("examples/fixed", "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["summary"]["fail"], 0)
        self.assertGreater(report["summary"]["unknown"], 0)
        self.assertEqual(self.run_cli("examples/fixed", "--strict").returncode, 1)

    def test_does_not_execute_readme_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("```sh\ntouch SHOULD_NOT_EXIST\n```\n")
            result = self.run_cli(root, "--format", "json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((root / "SHOULD_NOT_EXIST").exists())
            self.assertGreater(json.loads(result.stdout)["summary"]["unknown"], 0)

    def test_missing_readme_is_error_not_clean_result(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_cli(directory)
            self.assertEqual(result.returncode, 2)
            self.assertIn("coldstart:", result.stderr)

    def test_file_output_preserves_failure_status(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.html"
            result = self.run_cli("examples/broken", "--format", "html", "--output", output)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("ColdStart", output.read_text())
            self.assertEqual(result.stdout, "")

    def test_cannot_overwrite_readme_with_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            readme = root / "README.md"
            original = "# Nothing to check\n"
            readme.write_text(original)
            result = self.run_cli(root, "--output", readme)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(readme.read_text(), original)

    def test_text_output_escapes_source_and_manifest_control_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("```sh\nnpm run dev\n```\n```sh\nprintf '" + chr(27) + "[2J'\n```\n")
            (root / "package.json").write_text(json.dumps({"scripts": {"dev": "echo " + chr(155) + "2J"}}))
            result = self.run_cli(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(chr(27), result.stdout)
            self.assertNotIn(chr(155), result.stdout)
            self.assertIn(r"\x1b[2J", result.stdout)
            self.assertIn(r"\x9b2J", result.stdout)
            self.assertGreater(result.stdout.count("\n"), 3)

    def test_all_c0_c1_controls_are_visible_except_line_feed(self):
        controls = "".join(chr(number) for number in (*range(32), *range(127, 160)))
        sanitized = terminal_safe(controls)
        self.assertEqual(sanitized.count("\n"), 1)
        self.assertFalse(any(ord(char) < 32 and char != "\n" or 127 <= ord(char) <= 159 for char in sanitized))
        self.assertIn(r"\x1b", sanitized)
        self.assertIn(r"\x9f", sanitized)

    def test_other_stdout_formats_do_not_emit_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = "echo " + chr(155) + "2J"
            (root / "README.md").write_text("```sh\nnpm run dev\n```\n")
            (root / "package.json").write_text(json.dumps({"scripts": {"dev": evidence}}))
            for output_format in ("json", "markdown", "html"):
                with self.subTest(output_format=output_format):
                    result = self.run_cli(root, "--format", output_format)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn(chr(155), result.stdout)
                    if output_format == "json":
                        self.assertIn(evidence, json.loads(result.stdout)["checks"][0]["evidence"])

    def test_output_path_confirmation_is_terminal_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / ("report-" + chr(27) + ".txt")
            result = self.run_cli("examples/fixed", "--output", output)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output.is_file())
            self.assertNotIn(chr(27), result.stderr)
            self.assertIn(r"\x1b", result.stderr)

    def test_read_error_path_is_terminal_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subdir = root / ("web-" + chr(27))
            subdir.mkdir()
            (root / "README.md").write_text("```sh\ncd '" + subdir.name + "' && npm run dev\n```\n")
            (subdir / "package.json").write_text("{broken")
            result = self.run_cli(root)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn(chr(27), result.stderr)
            self.assertIn(r"\x1b", result.stderr)

    def test_argument_errors_are_terminal_safe(self):
        result = self.run_cli("--format", "invalid-" + chr(155))
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(chr(155), result.stderr)
        self.assertIn(r"\x9b", result.stderr)


if __name__ == "__main__":
    unittest.main()
