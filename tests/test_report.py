import unittest

from coldstart.report import render_html, render_markdown


class ReportTests(unittest.TestCase):
    def sample(self):
        return {
            "schema_version": 1, "project": '<img src=x onerror="alert(1)">',
            "readme": "README.md", "mode": "static",
            "context": "Synthetic demonstration fixture",
            "checks": [{"id": "C001", "kind": "script", "status": "fail",
                        "file": "README.md", "line": 7,
                        "command": "npm run <script>alert(1)</script>",
                        "message": 'Missing script <img src=x onerror="alert(2)">',
                        "evidence": "scripts: start, test"}],
            "summary": {"pass": 0, "fail": 1, "unknown": 0},
        }

    def test_untrusted_strings_are_escaped_in_html(self):
        output = render_html(self.sample())
        self.assertNotIn('<img src=x', output)
        self.assertNotIn("<script>alert(1)</script>", output)
        self.assertIn("&lt;", output)
        self.assertIn("Synthetic demonstration fixture", output)
        self.assertNotIn('src="http', output)
        self.assertNotIn('href="http', output)

    def test_markdown_contains_source_and_evidence(self):
        output = render_markdown(self.sample())
        self.assertIn(r"README\.md:7", output)
        self.assertIn("scripts: start, test", output)
        self.assertIn("Synthetic demonstration fixture", output)


if __name__ == "__main__":
    unittest.main()
