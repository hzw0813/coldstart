import tempfile
import unittest
from pathlib import Path

from coldstart.local_targets import inspect_local_target


class LocalTargetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()

    def write(self, path, content="raise RuntimeError('This file must never be executed')\n"):
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)
        return destination

    def inspect(self, *args, cwd=None):
        return inspect_local_target(self.root, cwd or self.root, list(args))

    def test_rich_like_package_main_is_local_reference_only(self):
        self.write("rich/__init__.py")
        self.write("rich/__main__.py")
        status, message, evidence = self.inspect("python", "-m", "rich")
        self.assertEqual(status, "pass")
        self.assertIn("reference", message)
        self.assertIn("rich/__main__.py", evidence)
        self.assertIn("unverified", evidence)

    def test_nanochat_like_namespace_module(self):
        self.write("scripts/chat_cli.py")
        result = self.inspect("python3", "-m", "scripts.chat_cli", "--help")
        self.assertEqual(result[0], "pass")
        self.assertIn("scripts/chat_cli.py", result[2])

    def test_regular_top_level_module(self):
        self.write("hello.py")
        self.assertEqual(self.inspect("python", "-m", "hello")[0], "pass")

    def test_nested_package_main(self):
        self.write("tools/runner/__main__.py")
        self.assertEqual(self.inspect("python3", "-m", "tools.runner")[0], "pass")

    def test_missing_module_is_unknown_not_broken(self):
        self.assertEqual(self.inspect("python", "-m", "external_dependency")[0], "unknown")

    def test_package_without_main_is_unknown(self):
        self.write("library/__init__.py")
        self.assertEqual(self.inspect("python", "-m", "library")[0], "unknown")

    def test_pip_commands_are_left_to_the_scanner(self):
        for module in ("pip", "pip._internal"):
            with self.subTest(module=module):
                self.assertIsNone(self.inspect("python", "-m", module, "install", "-r", "requirements.txt"))

    def test_module_names_are_literal_identifiers(self):
        for module in ("", ".foo", "foo.", "foo..bar", "../foo", "foo/bar", "foo-bar", "$MODULE", "class"):
            with self.subTest(module=module):
                self.assertEqual(self.inspect("python", "-m", module)[0], "unknown")

    def test_parent_module_file_is_ambiguous(self):
        self.write("tools.py")
        self.write("tools/runner.py")
        result = self.inspect("python", "-m", "tools.runner")
        self.assertEqual(result[0], "unknown")
        self.assertIn("parent", result[1])

    def test_module_and_package_priority_is_ambiguous(self):
        self.write("run.py")
        self.write("run/__init__.py")
        for has_main in (False, True):
            with self.subTest(has_main=has_main):
                if has_main:
                    self.write("run/__main__.py")
                self.assertEqual(self.inspect("python", "-m", "run")[0], "unknown")

    def test_nanochat_like_shell_target_is_not_executed(self):
        self.write("runs/speedrun.sh", "touch SHOULD_NOT_EXIST\n")
        for shell in ("bash", "sh", "zsh"):
            with self.subTest(shell=shell):
                result = self.inspect(shell, "runs/speedrun.sh")
                self.assertEqual(result[0], "pass")
                self.assertIn("runs/speedrun.sh", result[2])
        self.assertFalse((self.root / "SHOULD_NOT_EXIST").exists())

    def test_missing_shell_target_is_unknown(self):
        self.assertEqual(self.inspect("bash", "generated.sh")[0], "unknown")

    def test_shell_flags_and_other_commands_are_not_supported(self):
        for args in ([], ["node", "app.js"], ["python3", "-I", "-m", "thing"], ["bash", "-c", "echo hi"], ["sh", "--", "script.sh"]):
            with self.subTest(args=args):
                self.assertIsNone(self.inspect(*args))

    def test_dynamic_and_absolute_shell_targets_are_unknown(self):
        for target in ("", "$SCRIPT", "*.sh", "~/run.sh", "/tmp/run.sh", "scripts/a;b.sh"):
            with self.subTest(target=target):
                self.assertEqual(self.inspect("bash", target)[0], "unknown")

    def test_script_directory_is_not_regular_file(self):
        (self.root / "script.sh").mkdir()
        self.assertEqual(self.inspect("sh", "script.sh")[0], "unknown")

    def test_missing_intermediate_path_not_normalized_to_found(self):
        self.write("run.sh")
        self.assertEqual(self.inspect("bash", "missing/../run.sh")[0], "unknown")

    def test_outside_symlink_targets_are_unknown(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "run.py").write_text("raise RuntimeError('Do not import')\n")
        (outside / "run.sh").write_text("exit 1\n")
        (self.root / "run.py").symlink_to(outside / "run.py")
        (self.root / "run.sh").symlink_to(outside / "run.sh")
        (self.root / "pkg").symlink_to(outside, target_is_directory=True)
        for args in (("python", "-m", "run"), ("python", "-m", "pkg.run"), ("bash", "run.sh")):
            with self.subTest(args=args):
                self.assertEqual(self.inspect(*args)[0], "unknown")

    def test_outside_package_main_symlink_is_unknown(self):
        outside = Path(self.temp.name) / "outside.py"
        outside.write_text("raise RuntimeError('Do not import')\n")
        (self.root / "pkg").mkdir()
        (self.root / "pkg/__main__.py").symlink_to(outside)
        self.assertEqual(self.inspect("python", "-m", "pkg")[0], "unknown")

    def test_working_directory_is_respected(self):
        self.write("sub/main.py")
        self.assertEqual(self.inspect("python", "-m", "main", cwd=self.root / "sub")[0], "pass")
        self.assertEqual(self.inspect("python", "-m", "main")[0], "unknown")

    def test_outside_working_directory_is_unknown(self):
        self.assertEqual(self.inspect("python", "-m", "main", cwd=Path(self.temp.name))[0], "unknown")


if __name__ == "__main__":
    unittest.main()
