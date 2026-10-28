"""Read-only, deliberately narrow checks of README shell examples.

This module never starts a process or requests the network. A ``pass`` means a
reference exists in the current checkout, not that the command will run.
"""

from __future__ import annotations

import json
import re
import shlex
import stat
from pathlib import Path
from typing import Any

from .local_targets import inspect_local_target


MAX_TEXT_BYTES = 1_048_576
MAX_CHECKS = 2_000
SHELL_LANGUAGES = {"bash", "sh", "zsh", "shell", "console"}
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
SHELL_CONTROL = {"if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done", "case", "esac", "function", "select", "{", "}"}


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix() or "."


def _within(root: Path, cwd: Path, reference: str) -> Path | None:
    """Resolve references without following an out-of-root target for reading."""
    if not reference or Path(reference).is_absolute():
        return None
    if any(char in reference for char in "~$`*?[]{}"):
        return None
    try:
        candidate = cwd / reference
        resolved = candidate.resolve()
        resolved.relative_to(root)
        # Resolution establishes confinement only. Test/read the original path:
        # missing/../app.py does not exist even if resolve() produces app.py.
        return candidate
    except (OSError, RuntimeError, ValueError):
        return None


def _read_text(path: Path, label: str) -> str:
    try:
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"{label} must be a regular file.")
        if metadata.st_size > MAX_TEXT_BYTES:
            raise ValueError(f"{label} exceeds the 1 MiB input limit.")
        with path.open("rb") as handle:
            raw = handle.read(MAX_TEXT_BYTES + 1)
        if len(raw) > MAX_TEXT_BYTES:
            raise ValueError(f"{label} exceeds the 1 MiB input limit.")
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} is not UTF-8 text.") from exc
    except OSError as exc:
        raise ValueError(f"Cannot read {label}.") from exc


def _without_comment(command: str) -> str:
    """Remove shell comments without treating word#suffix as a comment."""
    quote = ""
    escaped = False
    for index, char in enumerate(command):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
        elif quote:
            if char == quote:
                quote = ""
        elif char in {"'", '"'}:
            quote = char
        elif char == "#" and (index == 0 or command[index - 1].isspace()):
            return command[:index]
    return command


def _has_quoted_metacharacters(command: str) -> bool:
    """Avoid interpreting literal operator arguments after shlex drops quotes."""
    quote = ""
    escaped = False
    for char in command:
        if escaped:
            if char in ";&|<>()":
                return True
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
        elif quote:
            if char == quote:
                quote = ""
            elif char in ";&|<>()":
                return True
        elif char in {"'", '"'}:
            quote = char
    return False


def _cd_traverses_symlink(root: Path, cwd: Path, reference: str) -> bool:
    """Check lexical components before resolving away logical cd semantics."""
    if Path(reference).is_absolute():
        return False  # Absolute references are rejected by _within.
    cursor = cwd
    try:
        for component in Path(reference).parts:
            cursor = cursor.parent if component == ".." else cursor / component
            try:
                cursor.relative_to(root)
            except ValueError:
                return True  # Do not inspect components outside the project.
            if cursor.is_symlink():
                return True
    except OSError:
        return True  # An unreadable component cannot establish reliable state.
    return False


def _shell_blocks(text: str):
    fence_char = ""
    fence_size = 0
    language = ""
    html_comment = False
    lines: list[tuple[int, str]] = []
    opening = 1
    for number, line in enumerate(text.splitlines(), 1):
        # CommonMark HTML comment blocks can contain apparent fences. Their
        # contents are not rendered instructions. A comment block ends on the
        # line containing -->; leave original line numbers untouched. Inside
        # any fenced block, <!-- is literal code and must not change this state.
        if not fence_char:
            if html_comment:
                if "-->" in line:
                    html_comment = False
                continue
            comment_start = re.match(r"^ {0,3}<!--", line)
            if comment_start:
                html_comment = "-->" not in line[comment_start.end():]
                continue
        match = FENCE.match(line)
        if not fence_char:
            if match:
                marker, info = match.groups()
                # Backticks in the info string are invalid CommonMark fences.
                if marker[0] == "`" and "`" in info:
                    continue
                fence_char, fence_size = marker[0], len(marker)
                language = info.strip().lower()
                lines, opening = [], number
        elif match and match.group(1)[0] == fence_char and len(match.group(1)) >= fence_size and not match.group(2).strip():
            if language in SHELL_LANGUAGES:
                yield language, lines, opening
            fence_char = ""
        else:
            lines.append((number, line))
    # CommonMark permits an unclosed fence to continue until end of document.
    if fence_char and language in SHELL_LANGUAGES:
        yield language, lines, opening


def scan(root: Path, readme: str = "README.md") -> dict[str, Any]:
    """Check static references in a README, returning a JSON-serializable report.

    Each fenced block begins at the repository root. Relative ``cd`` is tracked
    inside a block. An unknown or executed command makes subsequent state
    unknown; no file creation, shell expansion, or install lifecycle is assumed.
    Missing or invalid input raises ``ValueError``; unsupported examples produce
    ``unknown`` checks instead of a false clean bill of health.
    """
    try:
        root = Path(root).resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError("Cannot resolve the project directory.") from exc
    if not root.is_dir():
        raise ValueError("Project path must be an existing directory.")
    readme_path = _within(root, root, readme)
    if readme_path is None:
        raise ValueError("README must be a relative file inside the project.")
    if not readme_path.is_file():
        raise ValueError("README file does not exist or is not a regular file.")
    display_readme = _relative(readme_path, root)
    text = _read_text(readme_path, display_readme)
    checks: list[dict[str, Any]] = []
    manifests: dict[Path, dict[str, Any] | None] = {}

    def add(kind: str, status: str, line: int, command: str, message: str, evidence: str) -> None:
        if len(checks) >= MAX_CHECKS:
            raise ValueError("README exceeds the 2,000-check analysis limit.")
        checks.append({
            "id": f"C{len(checks) + 1:03d}", "kind": kind, "status": status,
            "file": display_readme, "line": line, "command": command,
            "message": message, "evidence": evidence,
        })

    def manifest(cwd: Path) -> tuple[dict[str, Any] | None, str]:
        location = cwd / "package.json"
        label = _relative(location, root)
        safe_location = _within(root, cwd, "package.json")
        if safe_location is None:
            return None, f"{label} resolves outside the project; it was not read."
        if safe_location in manifests:
            return manifests[safe_location], label
        if not safe_location.exists():
            manifests[safe_location] = None
            return None, f"No {label} exists in the current checkout."
        try:
            data = json.loads(_read_text(safe_location, label))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {label} (line {exc.lineno}).") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{label} must contain a JSON object.")
        scripts = data.get("scripts", {})
        if not isinstance(scripts, dict) or any(not isinstance(value, str) for value in scripts.values()):
            raise ValueError(f"{label} scripts must be an object of string commands.")
        manifests[safe_location] = data
        return data, label

    def check_file(cwd: Path, name: str, number: int, command: str) -> None:
        path = _within(root, cwd, name)
        if path is None:
            add("file", "unknown", number, command, "File reference cannot be checked within the project.", "Absolute paths, shell expansion, and paths outside the project are not inspected.")
        elif path.is_file():
            add("file", "pass", number, command, "Referenced file exists in this checkout.", _relative(path, root))
        else:
            add("file", "fail", number, command, "Referenced file is missing or is not a regular file.", _relative(path, root))

    for language, lines, opening in _shell_blocks(text):
        cwd = root
        uncertain = False
        for number, source in lines:
            command = source.strip()
            if not command or command.startswith("#"):
                continue
            if language == "console" and not command.startswith("$ "):
                # Console output is not an instruction to execute.
                continue
            if command.startswith("$ "):
                command = command[2:].strip()
            if not command or command.startswith("#"):
                continue
            if uncertain:
                add("unsupported", "unknown", number, command, "Earlier steps make this block's state uncertain.", "No commands were executed. Put independent examples in separate fenced blocks.")
                continue
            if any(char in command for char in "$`") or command.endswith("\\"):
                add("unsupported", "unknown", number, command, "Shell expansion or multiline commands are not modeled.", "The rest of this fenced block is treated as unknown.")
                uncertain = True
                continue
            uncommented = _without_comment(command)
            if _has_quoted_metacharacters(uncommented):
                add("unsupported", "unknown", number, command, "Quoted or escaped shell metacharacters are not modeled.", "Literal arguments must not be mistaken for command separators.")
                uncertain = True
                continue
            try:
                lexer = shlex.shlex(uncommented, posix=True, punctuation_chars=";&|<>()")
                lexer.whitespace_split = True
                lexer.commenters = ""
                tokens = list(lexer)
            except ValueError:
                add("unsupported", "unknown", number, command, "Cannot parse this shell example.", "Unbalanced quoting or unsupported shell syntax.")
                uncertain = True
                continue
            if not tokens:
                continue
            if any(re.fullmatch(r"[;&|<>()]+", token) and token != "&&" for token in tokens) or tokens[0] in SHELL_CONTROL:
                add("unsupported", "unknown", number, command, "Complex shell control flow is not modeled.", "Only simple commands and && chains are supported.")
                uncertain = True
                continue
            if any(any(char in token for char in "*?[]{}~") for token in tokens):
                add("unsupported", "unknown", number, command, "Shell patterns or dynamic selections are not modeled.", "Globs, home expansion, brace expansion, and script-name patterns require runtime verification.")
                uncertain = True
                continue
            groups: list[list[str]] = [[]]
            for token in tokens:
                if token == "&&":
                    groups.append([])
                else:
                    groups[-1].append(token)
            if any(not group for group in groups):
                add("unsupported", "unknown", number, command, "Incomplete shell command chain.", "Every && segment must contain a command.")
                uncertain = True
                continue
            for args in groups:
                fragment = shlex.join(args)
                if uncertain:
                    add("unsupported", "unknown", number, fragment, "An earlier command makes the next step uncertain.", "The scanner does not execute commands or assume their side effects.")
                    continue
                executable = args[0]
                if executable == "cd" and len(args) == 2:
                    if args[1].startswith("-"):
                        add("path", "unknown", number, fragment, "cd options and previous-directory lookup are not modeled.", "OLDPWD and shell-specific cd option semantics require runtime verification.")
                        uncertain = True
                        continue
                    if _cd_traverses_symlink(root, cwd, args[1]):
                        add("path", "unknown", number, fragment, "This working-directory path cannot be tracked reliably.", "Symlinks, out-of-project traversal, or unreadable components require runtime verification; logical and physical cd can differ.")
                        uncertain = True
                        continue
                    path = _within(root, cwd, args[1])
                    if path is None:
                        add("path", "unknown", number, fragment, "Working directory cannot be resolved inside the project.", "Absolute paths, shell expansion, and paths outside the project are not inspected.")
                        uncertain = True
                    elif not path.is_dir():
                        add("path", "fail", number, fragment, "Working directory is missing or is not a directory.", _relative(path, root))
                        uncertain = True
                    else:
                        # Existing, symlink-free cd paths can now be normalized.
                        cwd = path.resolve()
                        add("path", "pass", number, fragment, "Working directory exists in this checkout.", _relative(cwd, root))
                    continue
                script = None
                if executable in {"npm", "pnpm", "yarn", "bun"} and len(args) >= 3 and (args[1] == "run" or (executable == "npm" and args[1] == "run-script")) and not args[2].startswith("-"):
                    script = args[2]
                elif executable == "npm" and len(args) >= 2 and args[1] in {"test", "start", "stop", "restart"}:
                    script = args[1]
                if script is not None:
                    # Flags that change package lookup or request optional scripts
                    # invalidate simple current-directory script lookup.
                    flags = args[3:] if args[1] in {"run", "run-script"} else args[2:]
                    pre_separator = flags[:flags.index("--")] if "--" in flags else flags
                    if any(flag.startswith("-") for flag in pre_separator):
                        add("script", "unknown", number, fragment, "Package-manager flags may change script resolution.", "Workspace, directory, and optional-script flags are not modeled.")
                        uncertain = True
                        continue
                    data, label = manifest(cwd)
                    if data is None:
                        add("script", "unknown", number, fragment, "No local package manifest could establish this script.", f"{label} Ancestor and workspace lookup are not modeled.")
                    else:
                        scripts = data.get("scripts", {})
                        if script in scripts:
                            add("script", "pass", number, fragment, f"Script {script!r} is declared.", f"{label}: scripts.{script} = {scripts[script][:240]}")
                        elif executable == "npm" and script == "start":
                            server = _within(root, cwd, "server.js")
                            if server is not None and server.is_file():
                                add("script", "pass", number, fragment, "npm start has an implicit server.js target.", f"{_relative(server, root)} exists; command execution was not tested.")
                            else:
                                add("script", "unknown", number, fragment, "No explicit start script is declared.", "npm lifecycle defaults and generated server.js files require runtime verification.")
                        elif executable in {"yarn", "bun"} or (executable == "npm" and script == "restart"):
                            add("script", "unknown", number, fragment, f"No explicit {script!r} script is declared.", f"{label}; binary, file, or lifecycle fallbacks are not resolved.")
                        else:
                            available = ", ".join(sorted(scripts)) or "(none)"
                            add("script", "fail", number, fragment, f"Script {script!r} is not declared.", f"{label}: available scripts: {available[:300]}")
                    uncertain = True
                    continue
                if executable in {"python", "python3"} and len(args) >= 2 and not args[1].startswith("-") and args[1].endswith(".py"):
                    check_file(cwd, args[1], number, fragment)
                    uncertain = True
                    continue
                pip_args = args[1:] if executable in {"pip", "pip3"} else args[3:] if executable in {"python", "python3"} and args[1:3] == ["-m", "pip"] else []
                if len(pip_args) == 3 and pip_args[0:2] == ["install", "-r"]:
                    check_file(cwd, pip_args[2], number, fragment)
                    uncertain = True
                    continue
                local_target = inspect_local_target(root, cwd, args)
                if local_target is not None:
                    status, message, evidence = local_target
                    add("file", status, number, fragment, message, evidence)
                    uncertain = True
                    continue
                add("unsupported", "unknown", number, fragment, "This command is outside the static checker's supported scope.", "No command was executed; later steps in this block may depend on its effects.")
                uncertain = True

    if not checks:
        add("unsupported", "unknown", 1, "", "No supported shell examples were available to check.", "Use bash, sh, zsh, shell, or console fenced blocks; console commands need a $ prompt.")
    return {
        "schema_version": 1,
        "project": root.name,
        "readme": display_readme,
        "mode": "static",
        "checks": checks,
        "summary": {status: sum(check["status"] == status for check in checks) for status in ("pass", "fail", "unknown")},
    }
