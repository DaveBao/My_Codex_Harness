#!/usr/bin/env python3
"""Initialize a progressive-disclosure harness scaffold safely."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
SCAFFOLD = PACKAGE_ROOT / "scaffold"
MANAGED_FILES = {
    ".codex/config.toml",
    ".codex/agents/harness-builder.toml",
    ".codex/agents/harness-librarian.toml",
    ".codex/agents/harness-reviewer.toml",
    "docs/references/builder-handoff.schema.json",
    "docs/references/lifecycle-event.schema.json",
    "docs/references/worklog-events.md",
}
REPORT_KEYS = ("created", "replaced", "skipped", "conflicts")


class InitError(Exception):
    """Expected initialization failure safe to show without a traceback."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Initialize a non-destructive project harness scaffold."
    )
    parser.add_argument("--root", default=".", help="Target repository root (default: cwd)")
    parser.add_argument("--dry-run", action="store_true", help="Report actions without writing")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace conflicting harness-managed files; never replace project-owned state",
    )
    return parser.parse_args()


def empty_report() -> dict[str, list[str]]:
    return {key: [] for key in REPORT_KEYS}


def destination_issue(root: Path, destination: Path, expect_directory: bool) -> str | None:
    try:
        relative = destination.relative_to(root)
    except ValueError:
        return "escapes target root"

    current = root
    for index, part in enumerate(relative.parts):
        current /= part
        if current.is_symlink():
            return "symlink destination"
        if not current.exists():
            continue
        is_last = index == len(relative.parts) - 1
        if not is_last and not current.is_dir():
            return "parent is not a directory"
        if is_last and current.is_dir() != expect_directory:
            return "not a directory" if expect_directory else "not a regular file"
    return None


def copy_scaffold(root: Path, dry_run: bool, force: bool) -> dict[str, list[str]]:
    report = empty_report()

    for source in sorted(SCAFFOLD.rglob("*"), key=lambda path: path.relative_to(SCAFFOLD).as_posix()):
        relative = source.relative_to(SCAFFOLD)
        relative_text = relative.as_posix()
        destination = root / relative
        issue = destination_issue(root, destination, source.is_dir())
        if issue:
            report["conflicts"].append(f"{relative_text} ({issue})")
            continue

        if source.is_dir():
            if not destination.exists():
                report["created"].append(f"{relative_text}/")
                if not dry_run:
                    destination.mkdir(parents=True, exist_ok=True)
            continue

        if not destination.exists():
            report["created"].append(relative_text)
            if not dry_run:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            continue

        if destination.read_bytes() == source.read_bytes():
            report["skipped"].append(f"{relative_text} (unchanged)")
            continue

        if relative_text not in MANAGED_FILES:
            report["skipped"].append(f"{relative_text} (project-owned; preserved)")
            continue

        if not force:
            report["conflicts"].append(
                f"{relative_text} (managed file differs; rerun with --force to replace)"
            )
            continue

        report["replaced"].append(f"{relative_text} (managed conflict replaced with --force)")
        if not dry_run:
            shutil.copy2(source, destination)

    return report


def probe_git_root(root: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise InitError("Git executable was not found; install Git and retry") from error
    except (OSError, UnicodeError) as error:
        raise InitError("Git repository probe failed; verify Git is available") from error

    if result.returncode == 0:
        top_level = result.stdout.strip()
        if not top_level:
            raise InitError("Git repository probe returned no top level")
        try:
            resolved_top_level = Path(top_level).resolve()
        except (OSError, RuntimeError) as error:
            raise InitError("Git repository top level could not be resolved") from error
        if resolved_top_level != root:
            raise InitError(
                "target is inside an existing Git repository; rerun with --root at that "
                "repository's top level"
            )
        return resolved_top_level

    if "not a git repository" in result.stderr.lower():
        return None
    raise InitError(
        f"Git repository probe failed with exit {result.returncode}; "
        "verify repository state and permissions"
    )


def preflight_gitignore(root: Path) -> str | None:
    path = root / ".gitignore"
    issue = destination_issue(root, path, False)
    if issue:
        raise InitError(f".gitignore cannot be updated: {issue}")
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeError as error:
        raise InitError(".gitignore is not valid UTF-8; convert it to UTF-8 and retry") from error
    except OSError as error:
        raise InitError(".gitignore could not be read; verify its permissions") from error


def ensure_git(root: Path, dry_run: bool, git_root: Path | None) -> dict[str, list[str]]:
    report = empty_report()
    if git_root is not None:
        report["skipped"].append(".git/ (unchanged)")
    else:
        report["created"].append(".git/")
        if not dry_run:
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return report


def ensure_gitignore(root: Path, dry_run: bool, existing: str | None) -> dict[str, list[str]]:
    report = empty_report()
    path = root / ".gitignore"
    content = existing or ""
    additions = [entry for entry in (".worktrees/", ".DS_Store") if entry not in content.splitlines()]
    if existing is None:
        report["created"].append(".gitignore")
    elif additions:
        report["created"].append(".gitignore entries: " + ", ".join(additions))
    else:
        report["skipped"].append(".gitignore entries (unchanged)")

    if not dry_run and (additions or existing is None):
        separator = "" if not content or content.endswith("\n") else "\n"
        suffix = "\n".join(additions)
        if suffix:
            suffix += "\n"
        path.write_text(content + separator + suffix, encoding="utf-8")
    return report


def merge_reports(*reports: dict[str, list[str]]) -> dict[str, list[str]]:
    merged = empty_report()
    for report in reports:
        for key in REPORT_KEYS:
            merged[key].extend(report[key])
    return merged


def print_report(report: dict[str, list[str]]) -> None:
    for key in REPORT_KEYS:
        print(f"{key}:")
        if report[key]:
            for item in report[key]:
                print(f"  - {item}")
        else:
            print("  - none")


def main() -> int:
    args = parse_args()
    root = Path(args.root).expanduser().resolve()
    if not root.exists():
        print(f"init-project failed: target root does not exist: {root}", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"init-project failed: target root is not a directory: {root}", file=sys.stderr)
        return 2
    if not SCAFFOLD.is_dir():
        print(f"init-project failed: missing scaffold assets: {SCAFFOLD}", file=sys.stderr)
        return 2

    try:
        git_root = probe_git_root(root)
        gitignore = preflight_gitignore(root)
        report = merge_reports(
            ensure_git(root, args.dry_run, git_root),
            copy_scaffold(root, args.dry_run, args.force),
            ensure_gitignore(root, args.dry_run, gitignore),
        )
    except InitError as error:
        print(f"init-project failed: {error}", file=sys.stderr)
        return 2
    except (OSError, UnicodeError, subprocess.CalledProcessError) as error:
        print(f"init-project failed: {error}", file=sys.stderr)
        return 2

    print_report(report)
    return 1 if report["conflicts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
