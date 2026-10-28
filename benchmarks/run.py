#!/usr/bin/env python3
"""Collect static reports from a pinned public corpus; never run project code.

Exit 0 means collection succeeded, including reports with broken references.
Exit 2 means input, checkout integrity, scanning, or output failed. No network is
used unless --fetch is supplied; existing checkouts are never reset or updated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import coldstart  # noqa: E402
from coldstart.report import render_html  # noqa: E402
from coldstart.scanner import scan  # noqa: E402


MAX_MANIFEST_BYTES = 1_048_576
MAX_README_BYTES = 1_048_576
CONTEXT = (
    "Real-project static sample at a pinned commit. Project commands were not "
    "executed. This is not endorsement, adoption, or proof of successful installation. "
    "Unknown counts are analysis checkpoints, not coverage percentages or a complete command count."
)
FIELDS = {"id", "repository", "commit", "readme", "readme_sha256", "license", "sample_note"}


class BenchmarkError(ValueError):
    """A bounded, public-safe diagnostic about the benchmark inputs."""


def load_manifest(path: Path) -> list[dict[str, str]]:
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_MANIFEST_BYTES:
            raise BenchmarkError("Corpus manifest exceeds the 1 MiB limit.")
        rows = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BenchmarkError("Cannot read a valid UTF-8 JSON corpus manifest.") from exc
    if not isinstance(rows, list) or not rows or len(rows) > 100:
        raise BenchmarkError("Corpus manifest must be a nonempty list of at most 100 samples.")
    seen: set[str] = set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or set(row) != FIELDS:
            raise BenchmarkError(f"Sample {index} must contain exactly the documented manifest fields.")
        if any(not isinstance(value, str) for value in row.values()):
            raise BenchmarkError(f"Sample {index} fields must all be strings.")
        identifier = row["id"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,63}", identifier) or identifier.casefold() in seen:
            raise BenchmarkError(f"Sample {index} needs a unique safe slug (case-insensitive).")
        seen.add(identifier.casefold())
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", row["repository"]):
            raise BenchmarkError(f"Sample {identifier}: repository must be a GitHub owner/name, not a URL.")
        if row["repository"].split("/")[1] in {".", ".."}:
            raise BenchmarkError(f"Sample {identifier}: invalid repository name.")
        if not re.fullmatch(r"[0-9a-f]{40}", row["commit"]):
            raise BenchmarkError(f"Sample {identifier}: commit must be an exact lowercase 40-character SHA.")
        if not re.fullmatch(r"[0-9a-f]{64}", row["readme_sha256"]):
            raise BenchmarkError(f"Sample {identifier}: README hash must be an exact lowercase SHA-256.")
        readme = row["readme"]
        if (not readme or "\\" in readme or any(ord(c) < 32 for c in readme)
                or PurePosixPath(readme).is_absolute() or ".." in PurePosixPath(readme).parts
                or PurePosixPath(readme).as_posix() != readme or readme == "."):
            raise BenchmarkError(f"Sample {identifier}: README must be a normalized relative path inside the checkout.")
        if not row["license"].strip() or len(row["license"]) > 100 or len(row["sample_note"]) > 4_000:
            raise BenchmarkError(f"Sample {identifier}: invalid license or sample note.")
    return rows


def _git_environment() -> dict[str, str]:
    # Ignore inherited Git overrides, system/global filters, and credentials.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_SYSTEM": os.devnull})
    return env


def _git(checkout: Path, *arguments: str, extra: list[str] | None = None,
         allowed: tuple[int, ...] = (0,), timeout: int = 30) -> subprocess.CompletedProcess:
    argv = ["git", "--no-pager", "-c", f"core.hooksPath={os.devnull}",
            "-c", "core.fsmonitor=false", "-c", "credential.helper=",
            "-c", "protocol.allow=never", "-c", "protocol.https.allow=always"]
    argv.extend(extra or [])
    argv.extend(["-C", str(checkout), *arguments])
    try:
        result = subprocess.run(argv, shell=False, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", env=_git_environment(), timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BenchmarkError("Git is unavailable or the bounded Git operation timed out.") from exc
    if result.returncode not in allowed:
        # stderr can expose local paths, remote credentials, or config values.
        raise BenchmarkError(f"Git {arguments[0] if arguments else 'operation'} failed; inspect the checkout locally.")
    return result


def _filter_overrides(checkout: Path) -> list[str]:
    result = _git(checkout, "config", "--local", "--null", "--name-only", "--get-regexp",
                  r"^filter\..*\.(clean|smudge|process)$", allowed=(0, 1))
    overrides = []
    for key in result.stdout.split("\0"):
        if key:
            overrides.extend(["-c", f"{key}="])
            driver = key.rsplit(".", 1)[0]
            overrides.extend(["-c", f"{driver}.required=false"])
    return overrides


def verify_checkout(checkout: Path, sample: dict[str, str]) -> None:
    identifier = sample["id"]
    if not checkout.is_dir():
        raise BenchmarkError(f"Sample {identifier}: checkout is missing; use --fetch to obtain it explicitly.")
    top = _git(checkout, "rev-parse", "--show-toplevel").stdout.strip()
    if Path(top).resolve() != checkout.resolve():
        raise BenchmarkError(f"Sample {identifier}: directory is not its own Git checkout.")
    head = _git(checkout, "rev-parse", "--verify", "HEAD").stdout.strip()
    if head != sample["commit"]:
        raise BenchmarkError(f"Sample {identifier}: HEAD differs from the pinned commit; existing checkout was not changed.")
    filters = _filter_overrides(checkout)
    dirty = _git(checkout, "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching", extra=filters).stdout
    if dirty:
        raise BenchmarkError(f"Sample {identifier}: checkout contains tracked, untracked, or ignored changes; existing checkout was not changed.")


def verify_readme(checkout: Path, sample: dict[str, str]) -> None:
    identifier = sample["id"]
    candidate = checkout / sample["readme"]
    try:
        candidate.resolve().relative_to(checkout.resolve())
        if not candidate.is_file() or candidate.stat().st_size > MAX_README_BYTES:
            raise BenchmarkError(f"Sample {identifier}: README is missing, nonregular, or over 1 MiB.")
        with candidate.open("rb") as handle:
            raw = handle.read(MAX_README_BYTES + 1)
        if len(raw) > MAX_README_BYTES:
            raise BenchmarkError(f"Sample {identifier}: README is over 1 MiB.")
        if hashlib.sha256(raw).hexdigest() != sample["readme_sha256"]:
            raise BenchmarkError(f"Sample {identifier}: README hash differs from the corpus manifest.")
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, BenchmarkError):
            raise
        raise BenchmarkError(f"Sample {identifier}: README could not be read within the checkout.") from exc


def fetch_missing(checkout: Path, sample: dict[str, str]) -> None:
    """Create only a missing checkout; never alter an existing one."""
    if checkout.exists() or checkout.is_symlink():
        return
    verify_public_repository(sample["repository"])
    checkout.parent.mkdir(parents=True, exist_ok=True)
    try:
        checkout.mkdir()  # Atomically refuses an existing checkout.
    except FileExistsError:
        return
    try:
        _git(checkout, "init", "--quiet")
        _git(checkout, "remote", "add", "origin", f"https://github.com/{sample['repository']}.git")
        _git(checkout, "fetch", "--depth", "1", "--no-tags", "origin", sample["commit"], timeout=120)
        _git(checkout, "checkout", "--detach", "--quiet", "FETCH_HEAD")
        verify_checkout(checkout, sample)
        verify_readme(checkout, sample)
    except BaseException:
        # This directory was created by this invocation, not supplied by a user.
        shutil.rmtree(checkout)
        raise


def verify_public_repository(repository: str) -> None:
    """An unauthenticated metadata request must confirm public visibility."""
    request = Request(f"https://api.github.com/repos/{repository}",
                      headers={"Accept": "application/vnd.github+json", "User-Agent": "ColdStart-corpus-runner"})
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise BenchmarkError("Public repository metadata exceeded the input limit.")
        metadata = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise BenchmarkError("Cannot confirm public GitHub repository visibility without authentication.") from exc
    if (not isinstance(metadata, dict) or metadata.get("private") is not False
            or str(metadata.get("full_name", "")).casefold() != repository.casefold()):
        raise BenchmarkError("Fetch is limited to public repositories matching the corpus owner/name.")


def scanner_identity() -> dict[str, Any]:
    digest = hashlib.sha256()
    digest.update(coldstart.__version__.encode("utf-8") + b"\0")
    files = {}
    for path in sorted((PROJECT / "coldstart").rglob("*.py")):
        name = path.relative_to(PROJECT).as_posix()
        raw = path.read_bytes()
        files[name] = hashlib.sha256(raw).hexdigest()
        digest.update(name.encode("utf-8") + b"\0" + raw + b"\0")
    return {"version": coldstart.__version__, "source_sha256": digest.hexdigest(), "files": files}


def collect(manifest_path: Path, checkouts: Path, output: Path, fetch: bool = False) -> list[dict[str, Any]]:
    samples = load_manifest(manifest_path)
    scanner = scanner_identity()
    output_root = output.resolve()
    for sample in samples:
        checkout = (checkouts / sample["id"]).resolve()
        try:
            output_root.relative_to(checkout)
        except ValueError:
            continue
        raise BenchmarkError("Report output must not be inside an upstream checkout.")
    reports = []
    summaries = []
    for sample in samples:
        checkout = checkouts / sample["id"]
        if fetch:
            fetch_missing(checkout, sample)
        verify_checkout(checkout, sample)
        verify_readme(checkout, sample)
        try:
            report = scan(checkout, sample["readme"])
        except (OSError, ValueError) as exc:
            raise BenchmarkError(f"Sample {sample['id']}: static scanning failed; inspect the inputs locally.") from exc
        source_url = f"https://github.com/{sample['repository']}/blob/{sample['commit']}/{quote(sample['readme'], safe='/')}"
        provenance = {**sample, "source_url": source_url, "scanner": scanner,
                      "commands_executed": False, "context": CONTEXT}
        report.update({"project": sample["repository"], "context": CONTEXT, "provenance": provenance})
        # Detect accidental/concurrent changes during collection before publishing.
        verify_checkout(checkout, sample)
        verify_readme(checkout, sample)
        reports.append((sample["id"], report))
        summaries.append({**provenance, "summary": report["summary"],
                          "report_json": f"{sample['id']}.json", "report_html": f"{sample['id']}.html"})
    if scanner_identity() != scanner:
        raise BenchmarkError("Scanner source changed during collection; no reports were written.")
    # Validate all targets before writing any, including existing symlinks.
    targets = [output / "summary.json"]
    for identifier, _ in reports:
        targets.extend([output / f"{identifier}.json", output / f"{identifier}.html"])
    if any(path.is_symlink() or (path.exists() and not path.is_file()) for path in targets):
        raise BenchmarkError("A generated-report target is a symlink or nonregular file.")
    output.mkdir(parents=True, exist_ok=True)
    for identifier, report in reports:
        (output / f"{identifier}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (output / f"{identifier}.html").write_text(render_html(report), encoding="utf-8")
    (output / "summary.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summaries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--checkouts", required=True, type=Path, help="Directory containing one pinned checkout per corpus id")
    parser.add_argument("--output", required=True, type=Path, help="Directory for generated JSON, HTML, and summary.json")
    parser.add_argument("--fetch", action="store_true", help="Explicitly allow fetching missing public GitHub checkouts at pinned commits")
    args = parser.parse_args(argv)
    try:
        summaries = collect(Path(__file__).with_name("corpus.json"), args.checkouts, args.output, args.fetch)
    except (BenchmarkError, OSError) as exc:
        message = str(exc) if isinstance(exc, BenchmarkError) else "Local input/output operation failed."
        print(f"benchmark: {message}", file=sys.stderr)
        return 2
    for item in summaries:
        counts = item["summary"]
        print(f"{item['id']} @ {item['commit']}: {counts['pass']} found, {counts['fail']} broken, {counts['unknown']} unknown checkpoints")
    print("Collection complete. Static samples are not endorsement or adoption; commands were not executed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
