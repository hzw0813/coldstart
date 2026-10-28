"""Command-line entry point. Never executes the inspected project's commands."""

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .report import render_html, render_markdown
from .scanner import scan


def terminal_safe(value):
    """Make source-derived terminal controls visible, preserving line feeds."""
    return "".join(
        f"\\x{ord(char):02x}" if char != "\n" and (ord(char) < 32 or 127 <= ord(char) <= 159) else char
        for char in str(value)
    )


class SafeArgumentParser(argparse.ArgumentParser):
    def _print_message(self, message, file=None):
        # Invalid option values are echoed by argparse before main's try block.
        super()._print_message(terminal_safe(message) if message else message, file)


def render_text(report):
    labels = {"pass": "FOUND", "fail": "BROKEN", "unknown": "UNVERIFIED"}
    lines = [f"ColdStart {__version__} · {report['project']}",
             "Static README checks. No project commands were executed.", ""]
    for check in report["checks"]:
        lines.extend([
            f"{labels[check['status']]}  {check['file']}:{check['line']}",
            f"  {check['command']}", f"  {check['message']}",
            f"  Evidence: {check['evidence']}", "",
        ])
    counts = report["summary"]
    lines.append(f"{counts['fail']} broken · {counts['pass']} references found · {counts['unknown']} unverified")
    lines.append("References found does not mean installation succeeds. Review unverified steps separately.")
    return terminal_safe("\n".join(lines) + "\n")


def main(argv=None):
    parser = SafeArgumentParser(
        prog="coldstart", description="Find stale README command references without executing project code."
    )
    parser.add_argument("path", nargs="?", default=".", help="Local project directory (default: current directory)")
    parser.add_argument("--readme", default="README.md", help="README path relative to project root")
    parser.add_argument("--format", choices=["text", "json", "markdown", "html"], default="text")
    parser.add_argument("--output", type=Path, help="Write report to this file instead of stdout")
    parser.add_argument("--strict", action="store_true", help="Also exit 1 when any step is unverified")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    try:
        report = scan(Path(args.path), args.readme)
        if args.format == "json":
            # JSON's Unicode escapes keep C1 controls out of terminal output
            # while preserving the original values for JSON consumers.
            content = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
        elif args.format == "html":
            content = render_html(report)
        elif args.format == "markdown":
            content = render_markdown(report)
        else:
            content = render_text(report)
        if args.output:
            readme = (Path(args.path) / args.readme).resolve()
            if args.output.resolve() == readme:
                raise ValueError("Report output cannot overwrite the inspected README.")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(content, encoding="utf-8")
            print(terminal_safe(f"Report written: {args.output}"), file=sys.stderr)
        else:
            sys.stdout.write(terminal_safe(content))
        return int(report["summary"]["fail"] > 0 or
                   (args.strict and report["summary"]["unknown"] > 0))
    except (OSError, ValueError) as exc:
        print(terminal_safe(f"coldstart: {exc}"), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
