import json
import tempfile
import unittest
from pathlib import Path

from coldstart.scanner import MAX_TEXT_BYTES, scan


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def readme(self, content):
        self.write("README.md", content)

    def manifest(self, scripts, folder=""):
        self.write(str(Path(folder) / "package.json"), json.dumps({"scripts": scripts}))

    def checks(self):
        return scan(self.root)["checks"]

    def test_missing_script_has_source_and_manifest_evidence(self):
        self.readme("# Demo\n\n```sh\nnpm run start-old\n```\n")
        self.manifest({"dev": "vite"})
        result = self.checks()[0]
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["line"], 4)
        self.assertIn("dev", result["evidence"])
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_cd_chain_tracks_directory(self):
        self.readme("```bash\ncd web && npm run dev\n```\n")
        self.manifest({"dev": "vite"}, "web")
        self.assertEqual([x["status"] for x in self.checks()], ["pass", "pass"])
        self.assertIn("web/package.json", self.checks()[1]["evidence"])

    def test_directory_resets_between_blocks(self):
        self.readme("```sh\ncd web\n```\n```sh\nnpm run root\n```\n")
        self.manifest({}, "web")
        self.manifest({"root": "echo yes"})
        self.assertEqual([x["status"] for x in self.checks()], ["pass", "pass"])

    def test_missing_cd_blocks_downstream_assertions(self):
        self.readme("```sh\ncd missing && npm run dev\n```\n")
        self.manifest({"dev": "vite"})
        self.assertEqual([x["status"] for x in self.checks()], ["fail", "unknown"])

    def test_cd_options_and_oldpwd_are_unknown(self):
        for argument in ("-", "--", "-P", "-L"):
            with self.subTest(argument=argument):
                self.readme(f"```sh\ncd {argument} && npm run dev\n```\n")
                self.assertEqual([x["status"] for x in self.checks()], ["unknown", "unknown"])

    def test_quoted_and_escaped_operators_do_not_split_commands(self):
        commands = ('cd . "&&" npm run missing', "cd . '&&' npm run missing", r"cd . \&\& npm run missing")
        self.manifest({"dev": "vite"})
        for command in commands:
            with self.subTest(command=command):
                self.readme(f"```sh\n{command}\nnpm run missing\n```\n")
                self.assertEqual([x["status"] for x in self.checks()], ["unknown", "unknown"])

    def test_cd_through_internal_symlink_does_not_assume_physical_cwd(self):
        self.manifest({"dev": "vite"})
        self.manifest({}, "nested")
        (self.root / "nested/deep/child").mkdir(parents=True)
        (self.root / "alias").symlink_to("nested/deep", target_is_directory=True)
        for target in ("alias", "alias/child"):
            with self.subTest(target=target):
                self.readme(f"```sh\ncd {target}\ncd ..\nnpm run dev\n```\n")
                self.assertEqual([x["status"] for x in self.checks()], ["unknown", "unknown", "unknown"])

    def test_missing_path_component_is_not_erased_by_parent_traversal(self):
        self.write("app.py", "print('example')")
        self.readme("```sh\npython missing/../app.py\n```\n```sh\ncd missing/..\n```\n")
        self.assertEqual([x["status"] for x in self.checks()], ["fail", "fail"])

    def test_existing_parent_traversal_checks_original_path(self):
        self.write("app.py", "print('example')")
        (self.root / "existing").mkdir()
        self.readme("```sh\npython existing/../app.py\n```\n")
        self.assertEqual(self.checks()[0]["status"], "pass")

    def test_quoted_directory_and_comments(self):
        self.readme("```sh\ncd 'web app' # note\nnpm run dev -- --port 1000\n```\n")
        self.manifest({"dev": "vite"}, "web app")
        self.assertEqual([x["status"] for x in self.checks()], ["pass", "pass"])

    def test_complex_shell_is_unknown(self):
        for command in ("npm run dev | cat", "if true; then npm run dev; fi", "cd $PROJECT", "npm run dev || true", "npm run dev > result.txt", "npm run dev &>log.txt", "npm run dev*"):
            with self.subTest(command=command):
                self.readme(f"```sh\n{command}\n```\n")
                self.assertEqual(self.checks()[0]["status"], "unknown")

    def test_unsupported_creation_makes_next_step_unknown(self):
        self.readme("```sh\nmkdir generated\ncd generated\n```\n")
        self.assertEqual([x["status"] for x in self.checks()], ["unknown", "unknown"])
        self.assertFalse((self.root / "generated").exists())

    def test_script_execution_does_not_imply_generated_files(self):
        self.readme("```sh\nnpm run setup\npython generated.py\n```\n")
        self.manifest({"setup": "touch generated.py"})
        self.assertEqual([x["status"] for x in self.checks()], ["pass", "unknown"])
        self.assertFalse((self.root / "generated.py").exists())

    def test_requirements_file_and_python_source(self):
        self.readme("```sh\npip install -r requirements.txt\n```\n```sh\npython app.py\n```\n")
        self.write("requirements.txt", "")
        self.assertEqual([x["status"] for x in self.checks()], ["pass", "fail"])

    def test_python_module_pip(self):
        self.readme("```sh\npython3 -m pip install -r requirements-dev.txt\n```\n")
        self.write("requirements-dev.txt", "")
        self.assertEqual(self.checks()[0]["status"], "pass")

    def test_console_ignores_output(self):
        self.readme("```console\n$ npm run dev\n> Starting\nready\n```\n")
        self.manifest({"dev": "vite"})
        self.assertEqual(len(self.checks()), 1)

    def test_non_shell_fences_not_parsed(self):
        self.readme("```python\nnpm run missing\n```\n")
        self.assertEqual(self.checks()[0]["status"], "unknown")

    def test_html_comment_hides_fences_without_changing_source_lines(self):
        self.readme("# Demo\n\n<!-- hidden instructions\n```sh\nnpm run removed\n```\n-->\n\n```sh\nnpm run dev\n```\n")
        self.manifest({"dev": "vite"})
        checks = self.checks()
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]["status"], "pass")
        self.assertEqual(checks[0]["line"], 10)

    def test_unclosed_html_comment_does_not_expose_hidden_commands(self):
        self.readme("   <!-- old instructions\n```sh\nnpm run removed\n```\n")
        self.manifest({})
        report = scan(self.root)
        self.assertEqual(report["summary"]["fail"], 0)
        self.assertEqual(report["summary"]["unknown"], 1)
        self.assertEqual(report["checks"][0]["command"], "")

    def test_comment_marker_inside_fenced_code_does_not_hide_later_blocks(self):
        self.readme("```text\n<!-- literal, not an HTML comment\n```\n```sh\nnpm run dev\n```\n")
        self.manifest({"dev": "vite"})
        self.assertEqual([item["status"] for item in self.checks()], ["pass"])
        self.assertEqual(self.checks()[0]["line"], 5)

    def test_one_line_html_comment_does_not_hide_next_fence(self):
        self.readme("<!-- note -->\n```sh\nnpm run missing\n```\n")
        self.manifest({})
        self.assertEqual(self.checks()[0]["status"], "fail")
        self.assertEqual(self.checks()[0]["line"], 3)

    def test_invalid_manifest_is_input_error(self):
        self.readme("```sh\nnpm run dev\n```\n")
        self.write("package.json", "{broken")
        with self.assertRaisesRegex(ValueError, "Invalid JSON"):
            scan(self.root)

    def test_manifest_type_is_validated(self):
        self.readme("```sh\nnpm run dev\n```\n")
        self.write("package.json", '{"scripts": {"dev": 3}}')
        with self.assertRaisesRegex(ValueError, "string commands"):
            scan(self.root)

    def test_missing_readme_is_input_error(self):
        with self.assertRaisesRegex(ValueError, "README"):
            scan(self.root)

    def test_outside_readme_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "inside"):
            scan(self.root, "../README.md")

    def test_outside_directory_and_symlink_not_followed(self):
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside)
            (external / "package.json").write_text("invalid private content")
            (self.root / "external").symlink_to(external, target_is_directory=True)
            self.readme("```sh\ncd external && npm run dev\n```\n")
            self.assertEqual([x["status"] for x in self.checks()], ["unknown", "unknown"])

    def test_outside_manifest_symlink_is_unknown(self):
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / "package.json"
            external.write_text("invalid private content")
            (self.root / "package.json").symlink_to(external)
            self.readme("```sh\nnpm run dev\n```\n")
            self.assertEqual(self.checks()[0]["status"], "unknown")

    def test_workspace_flags_are_unknown(self):
        self.readme("```sh\nnpm run dev --workspace web\n```\n")
        self.manifest({"dev": "vite"})
        self.assertEqual(self.checks()[0]["status"], "unknown")

    def test_npm_default_start_and_restart(self):
        self.readme("```sh\nnpm start\n```\n```sh\nnpm restart\n```\n")
        self.manifest({})
        self.write("server.js", "")
        self.assertEqual([x["status"] for x in self.checks()], ["pass", "unknown"])

    def test_yarn_and_bun_binary_fallback_not_false_failure(self):
        self.readme("```sh\nyarn run eslint\n```\n```sh\nbun run vite\n```\n")
        self.manifest({})
        self.assertEqual([x["status"] for x in self.checks()], ["unknown", "unknown"])

    def test_pnpm_explicit_missing_script_fails(self):
        self.readme("```sh\npnpm run build\n```\n")
        self.manifest({"dev": "vite"})
        self.assertEqual(self.checks()[0]["status"], "fail")

    def test_missing_local_manifest_does_not_assume_no_ancestor(self):
        self.readme("```sh\ncd web && npm run dev\n```\n")
        (self.root / "web").mkdir()
        self.manifest({"dev": "vite"})
        self.assertEqual([x["status"] for x in self.checks()], ["pass", "unknown"])

    def test_hash_inside_word_is_not_a_comment(self):
        self.readme("```sh\nnpm run dev#web # actual comment\n```\n")
        self.manifest({"dev#web": "vite"})
        self.assertEqual(self.checks()[0]["status"], "pass")

    def test_large_input_rejected(self):
        self.readme("x" * (MAX_TEXT_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "limit"):
            scan(self.root)

    def test_tilde_fence_and_unclosed_fence_supported(self):
        self.readme("~~~sh\nnpm run dev\n~~~\n```sh\nnpm run dev")
        self.manifest({"dev": "vite"})
        self.assertEqual(scan(self.root)["summary"], {"pass": 2, "fail": 0, "unknown": 0})


if __name__ == "__main__":
    unittest.main()
