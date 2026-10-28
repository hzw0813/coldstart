"""Regenerate the labelled synthetic reports without running fixture commands.

Run from the ColdStart project root:
    python3 -m examples.generate_reports
"""

import json
from pathlib import Path

from coldstart.report import render_html, render_markdown
from coldstart.scanner import scan


def main():
    root = Path(__file__).resolve().parents[1]
    for fixture in ("broken", "fixed"):
        report = scan(root / "examples" / fixture)
        report["context"] = "Synthetic demo fixture — not a scan of a real public project."
        report["project"] = "Demo app / " + ("stale instructions" if fixture == "broken" else "references repaired")
        target = root / "docs" / ("demo-" + fixture)
        target.with_suffix(".html").write_text(render_html(report), encoding="utf-8")
        target.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
        target.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if fixture == "broken":
            page = render_html(report)
            navigation = ('<div class="edition" style="display:flex;gap:16px;flex-wrap:wrap">'
                          '<a href="https://github.com/hzw0813/coldstart">GitHub</a>'
                          '<a href="https://github.com/hzw0813/coldstart/blob/main/docs/real-world-study.md">8-project study</a>'
                          '<a href="study/reports/nanochat.html">Real report</a></div>')
            page = page.replace('<div class="edition">Repository evidence / v0.1</div>', navigation)
            (root / "docs/index.html").write_text(page, encoding="utf-8")
            (root / "docs/.nojekyll").touch()
        print(f"{fixture}: {report['summary']}")


if __name__ == "__main__":
    main()
