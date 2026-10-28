"""Inspect local entry-point references without importing or executing them.

The result describes files in the supplied checkout, not Python's eventual
import resolution or a shell script's ability to run in an environment.
"""

from __future__ import annotations

import keyword
import re
from pathlib import Path


_MODULE_PART = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_DYNAMIC = set("~$`*?[]{};&|<>()\n\r")
_LIMIT = "Local reference only; runtime and environment resolution are unverified."


def _unknown(message: str, evidence: str) -> tuple[str, str, str]:
    return "unknown", message, evidence


def _safe_path(root: Path, candidate: Path) -> Path | None:
    """Use resolution only for containment; preserve lexical existence checks."""
    try:
        candidate.resolve().relative_to(root)
        return candidate
    except (OSError, RuntimeError, ValueError):
        return None


def _inspect_module(root: Path, cwd: Path, module: str) -> tuple[str, str, str]:
    parts = module.split(".")
    if any(not _MODULE_PART.fullmatch(part) or keyword.iskeyword(part) for part in parts):
        return _unknown("This module name is outside the supported static syntax.",
                        "Only literal dotted identifier names are inspected; no module was imported.")

    directory = cwd
    for part in parts[:-1]:
        possible_file = _safe_path(root, directory / (part + ".py"))
        package = _safe_path(root, directory / part)
        if possible_file is None or package is None:
            return _unknown("The local module reference cannot be inspected within the project.",
                            "An entry-point or parent path resolves outside the chosen root.")
        if possible_file.exists():
            return _unknown("A parent module file makes this dotted reference ambiguous.",
                            f"{possible_file.relative_to(root).as_posix()} exists; package resolution was not evaluated.")
        if not package.is_dir():
            return _unknown("No local parent package directory establishes this reference.",
                            "The module may come from an installed dependency or another import path.")
        # Namespace-package directories are allowed as local reference evidence.
        # We do not claim that Python will select this directory at runtime.
        directory = package

    module_file = _safe_path(root, directory / (parts[-1] + ".py"))
    package = _safe_path(root, directory / parts[-1])
    if module_file is None or package is None:
        return _unknown("The local module reference cannot be inspected within the project.",
                        "An entry-point path resolves outside the chosen root.")
    if module_file.exists() and package.exists():
        return _unknown("Both local module and package paths exist for this name.",
                        "Module/package priority is not evaluated; no entry point was imported.")
    if module_file.is_file():
        return "pass", "A local module entry-point reference exists.", f"{module_file.relative_to(root).as_posix()}; {_LIMIT}"
    if package.is_dir():
        main_file = _safe_path(root, package / "__main__.py")
        if main_file is None:
            return _unknown("The local package entry point resolves outside the project.",
                            "The target was not inspected or imported.")
        if main_file.is_file():
            return "pass", "A local package entry-point reference exists.", f"{main_file.relative_to(root).as_posix()}; {_LIMIT}"
    return _unknown("No local entry-point file establishes this module reference.",
                    "It may come from an installed dependency, generated files, or another import path.")


def inspect_local_target(root: Path, cwd: Path, args: list[str]) -> tuple[str, str, str] | None:
    """Return a local-reference observation for supported commands, else None.

    Supported forms are ``python[3] -m dotted.name`` (except pip) and
    ``bash|sh|zsh literal/path``. Following arguments belong to the referenced
    program and are not validated. Missing references remain unknown because
    tutorials, installs, and external module paths may supply them later.
    """
    if not args:
        return None
    is_module = len(args) >= 3 and args[0] in {"python", "python3"} and args[1] == "-m"
    is_shell = len(args) >= 2 and args[0] in {"bash", "sh", "zsh"} and not args[1].startswith("-")
    if not is_module and not is_shell:
        return None
    if is_module and (args[2] == "pip" or args[2].startswith("pip.")):
        return None
    try:
        root = Path(root).resolve()
        cwd = Path(cwd).resolve()
        cwd.relative_to(root)
        if not root.is_dir() or not cwd.is_dir():
            return _unknown("The local working directory is unavailable.", "No entry-point reference was inspected.")
        if is_module:
            return _inspect_module(root, cwd, args[2])
        reference = args[1]
        if not reference or Path(reference).is_absolute() or any(char in _DYNAMIC for char in reference):
            return _unknown("This shell script reference is outside the supported static syntax.",
                            "Only literal relative script paths inside the chosen root are inspected.")
        target = _safe_path(root, cwd / reference)
        if target is None:
            return _unknown("The shell script reference resolves outside the project.", "The target was not inspected or executed.")
        if target.is_file():
            return "pass", "A local shell script reference exists.", f"{target.relative_to(root).as_posix()}; {_LIMIT}"
        return _unknown("No local file establishes this shell script reference.",
                        "A tutorial, installation, or earlier command may provide the file; no command was executed.")
    except (OSError, RuntimeError, ValueError):
        return _unknown("The local reference could not be inspected within the project.",
                        "Path containment or filesystem access could not be established.")
