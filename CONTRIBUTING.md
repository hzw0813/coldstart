# Contributing

ColdStart prefers an honest unknown over a confident false positive. Start a change with a small, sanitized README fixture and the relevant local files. Describe the command's expected semantics and link the package manager's official documentation when behavior is ambiguous.

Run `python3 -m unittest discover -s tests -v` from the project root. Add regression tests when changing parsing, path handling, state tracking, or report escaping. Never make scanning execute commands from the inspected project.

Do not submit secrets, personal repository contents, or unsupported claims that a project is broken. A report about a public project should include its commit, ColdStart version, exact source line, and a human check of the finding.

Useful early tasks:

- Test one documented command form against the package manager's actual semantics.
- Submit a reproducible false positive or unsupported syntax example.
- Improve accessibility or readability of reports.
- Design an opt-in runtime verification stage separately from the static checker.

The maintainer reviews scope, correctness, compatibility, and regression tests before accepting a change. No response-time guarantee is established during this initial release.
