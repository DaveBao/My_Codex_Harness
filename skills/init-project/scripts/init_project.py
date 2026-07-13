#!/usr/bin/env python3
"""Initialize a progressive-disclosure harness scaffold safely."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
SCAFFOLD = PACKAGE_ROOT / "scaffold"
MANAGED_FILES = {
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


def path_mode(path: Path) -> int | None:
    try:
        return path.lstat().st_mode
    except FileNotFoundError:
        return None


def destination_issue(root: Path, destination: Path, expect_directory: bool) -> str | None:
    try:
        relative = destination.relative_to(root)
    except ValueError:
        return "escapes target root"

    current = root
    for index, part in enumerate(relative.parts):
        current /= part
        mode = path_mode(current)
        if mode is None:
            continue
        if stat.S_ISLNK(mode):
            return "symlink destination"
        is_last = index == len(relative.parts) - 1
        if not is_last and not stat.S_ISDIR(mode):
            return "parent is not a directory"
        if is_last and expect_directory and not stat.S_ISDIR(mode):
            return "not a directory"
        if is_last and not expect_directory and not stat.S_ISREG(mode):
            return "not a regular file"
    return None


def probe_git_root(root: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-prefix"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise InitError("Git executable was not found; install Git and retry") from error
    except (OSError, UnicodeError) as error:
        raise InitError("Git repository probe failed; verify Git is available") from error

    if result.returncode == 0:
        if result.stdout not in ("", "\n"):
            raise InitError(
                "target is inside an existing Git repository; rerun with --root at that "
                "repository's top level"
            )
        return root
    if "not a git repository" in result.stderr.lower():
        return None
    raise InitError(
        f"Git repository probe failed with exit {result.returncode}; "
        "verify repository state and permissions"
    )


def read_gitignore(root: Path, report: dict[str, list[str]]) -> bytes | None:
    path = root / ".gitignore"
    issue = destination_issue(root, path, False)
    if issue:
        report["conflicts"].append(f".gitignore ({issue})")
        return None
    if not path.exists():
        return None
    try:
        content = path.read_bytes()
        content.decode("utf-8")
        return content
    except UnicodeError as error:
        raise InitError(".gitignore is not valid UTF-8; convert it to UTF-8 and retry") from error
    except OSError as error:
        raise InitError(".gitignore could not be read; verify its permissions") from error


def gitignore_content(existing: bytes | None) -> tuple[bytes, list[str]]:
    content = existing or b""
    lines = content.decode("utf-8").splitlines()
    additions = [entry for entry in (".worktrees/", ".DS_Store") if entry not in lines]
    if not additions:
        return content, []
    if b"\r\n" in content:
        newline = b"\r\n"
    elif b"\r" in content and b"\n" not in content:
        newline = b"\r"
    else:
        newline = b"\n"
    separator = b"" if not content or content.endswith((b"\n", b"\r")) else newline
    appended = newline.join(entry.encode("utf-8") for entry in additions) + newline
    return content + separator + appended, additions


def build_plan(root: Path, force: bool) -> tuple[list[tuple], dict[str, list[str]]]:
    plan: list[tuple] = []
    report = empty_report()
    git_root = probe_git_root(root)
    gitignore = read_gitignore(root, report)

    if git_root is None:
        report["created"].append(".git/")
        plan.append(("git-init", root))
    else:
        report["skipped"].append(".git/ (unchanged)")

    for source in sorted(
        SCAFFOLD.rglob("*"), key=lambda path: path.relative_to(SCAFFOLD).as_posix()
    ):
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
                plan.append(("mkdir", destination))
            continue

        if not destination.exists():
            report["created"].append(relative_text)
            plan.append(("copy-create", source, destination))
            continue

        current = destination.read_bytes()
        if current == source.read_bytes():
            report["skipped"].append(f"{relative_text} (unchanged)")
        elif relative_text not in MANAGED_FILES:
            report["skipped"].append(f"{relative_text} (project-owned; preserved)")
        elif not force:
            report["conflicts"].append(
                f"{relative_text} (managed file differs; rerun with --force to replace)"
            )
        else:
            report["replaced"].append(
                f"{relative_text} (managed conflict replaced with --force)"
            )
            plan.append(
                (
                    "copy-replace",
                    source,
                    destination,
                    current,
                    stat.S_IMODE(destination.stat().st_mode),
                )
            )

    if not report["conflicts"]:
        updated_ignore, additions = gitignore_content(gitignore)
        ignore_path = root / ".gitignore"
        if gitignore is None:
            report["created"].append(".gitignore")
            plan.append(("write-create", ignore_path, updated_ignore, 0o644))
        elif additions:
            report["created"].append(".gitignore entries: " + ", ".join(additions))
            plan.append(
                (
                    "write-replace",
                    ignore_path,
                    updated_ignore,
                    gitignore,
                    stat.S_IMODE(ignore_path.stat().st_mode),
                )
            )
        else:
            report["skipped"].append(".gitignore entries (unchanged)")

    return plan, report


def temporary_path(destination: Path) -> tuple[int, Path]:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.init-project-", dir=destination.parent
    )
    return descriptor, Path(name)


def atomic_copy(source: Path, destination: Path) -> None:
    descriptor, temporary = temporary_path(destination)
    os.close(descriptor)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write(destination: Path, content: bytes, mode: int) -> None:
    descriptor, temporary = temporary_path(destination)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def remove_created(path: Path) -> None:
    mode = path_mode(path)
    if mode is None:
        return
    if stat.S_ISDIR(mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def rollback(journal: list[tuple]) -> bool:
    complete = True
    for entry in reversed(journal):
        action, path, *details = entry
        try:
            if action == "remove":
                remove_created(path)
            elif action == "remove-file":
                mode = path_mode(path)
                if mode is None:
                    continue
                if not stat.S_ISREG(mode) or path.read_bytes() != details[0]:
                    complete = False
                    continue
                path.unlink()
            elif action == "rmdir":
                path.rmdir()
            elif action == "restore":
                old_content, old_mode, expected_content = details
                mode = path_mode(path)
                if mode is None or not stat.S_ISREG(mode):
                    complete = False
                    continue
                current = path.read_bytes()
                if current == old_content:
                    continue
                if current != expected_content:
                    complete = False
                    continue
                atomic_write(path, old_content, old_mode)
        except OSError:
            complete = False
    return complete


def execute_plan(root: Path, plan: list[tuple]) -> None:
    journal: list[tuple] = []
    try:
        for entry in plan:
            action, *details = entry
            if action == "git-init":
                git_path = root / ".git"
                try:
                    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
                except (OSError, subprocess.CalledProcessError):
                    if path_mode(git_path) is not None:
                        remove_created(git_path)
                    raise
                journal.append(("remove", git_path))
            elif action == "mkdir":
                path = details[0]
                path.mkdir()
                journal.append(("rmdir", path))
            elif action == "copy-create":
                source, destination = details
                expected_content = source.read_bytes()
                journal.append(("remove-file", destination, expected_content))
                atomic_copy(source, destination)
            elif action == "copy-replace":
                source, destination, old_content, old_mode = details
                expected_content = source.read_bytes()
                journal.append(
                    ("restore", destination, old_content, old_mode, expected_content)
                )
                atomic_copy(source, destination)
            elif action == "write-create":
                destination, content, mode = details
                journal.append(("remove-file", destination, content))
                atomic_write(destination, content, mode)
            elif action == "write-replace":
                destination, content, old_content, old_mode = details
                journal.append(("restore", destination, old_content, old_mode, content))
                atomic_write(destination, content, old_mode)
            else:
                raise OSError("unknown initialization action")
    except (OSError, UnicodeError, subprocess.CalledProcessError) as error:
        restored = rollback(journal)
        state = "all initializer changes were rolled back" if restored else "rollback was incomplete"
        raise InitError(f"write failed; {state}") from error


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
        plan, report = build_plan(root, args.force)
        if report["conflicts"]:
            print_report(report)
            return 1
        if not args.dry_run:
            execute_plan(root, plan)
    except InitError as error:
        print(f"init-project failed: {error}", file=sys.stderr)
        return 2
    except (OSError, UnicodeError, subprocess.CalledProcessError) as error:
        print(f"init-project failed: {error}", file=sys.stderr)
        return 2

    print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
