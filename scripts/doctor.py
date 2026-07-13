#!/usr/bin/env python3
"""Diagnose prerequisites, an installation, or an offline bundle."""

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deploy_model import load_json_object, validate_home, validate_target  # noqa: E402
from package_model import parse_agents_table  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]


def _canonical_manifest_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("bundle manifest path must be canonical and relative")
    pure = PurePosixPath(value)
    if pure.is_absolute() or pure.as_posix() != value or any(part in ("", ".", "..") for part in pure.parts):
        raise ValueError("bundle manifest path must be canonical and relative")
    return value


def _check_prerequisites(home: Path, require_codex: bool) -> list[str]:
    if sys.version_info < (3, 10):
        raise ValueError("Python 3.10 or newer is required")
    validate_home(home)
    existing = home
    while not existing.exists():
        existing = existing.parent
    if not os.access(existing, os.W_OK):
        raise ValueError(f"HOME is not writable: {home}")
    validate_target(home, home / ".codex")
    validate_target(home, home / ".agents")
    if shutil.which("git") is None:
        raise ValueError("Git executable not found")
    if require_codex and shutil.which("codex") is None:
        raise ValueError("Codex executable not found")
    config = home / ".codex/config.toml"
    if config.exists():
        if config.is_symlink() or not config.is_file():
            raise ValueError("config.toml must be a regular file")
        try:
            parse_agents_table(config.read_text(encoding="utf-8"))
        except UnicodeDecodeError as error:
            raise ValueError("config.toml must be UTF-8") from error
    with tempfile.TemporaryDirectory(dir=existing) as directory:
        root = Path(directory)
        target = root / "target"
        link = root / "link"
        target.mkdir()
        try:
            link.symlink_to(target, target_is_directory=True)
            symlinks = link.is_symlink()
        except OSError:
            symlinks = False
    return [
        f"Python: {sys.version_info.major}.{sys.version_info.minor}",
        f"Git: {shutil.which('git')}",
        f"Codex: {shutil.which('codex') or 'not requested/not found'}",
        f"HOME: {home}",
        f"Symlinks: {'available' if symlinks else 'copy fallback required'}",
    ]


def _verify_bundle(archive: Path) -> dict:
    if archive.is_symlink() or not archive.is_file():
        raise ValueError("bundle archive must be a regular file")
    sidecar = archive.with_name(archive.name + ".sha256")
    manifest_path = archive.parent / "bundle-manifest.json"
    if not sidecar.is_file() or sidecar.is_symlink():
        raise ValueError("bundle checksum sidecar is missing")
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("bundle manifest is missing")
    fields = sidecar.read_text(encoding="utf-8").strip().split()
    if len(fields) != 2 or fields[1] != archive.name:
        raise ValueError("invalid bundle checksum sidecar")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if fields[0] != digest:
        raise ValueError("bundle checksum mismatch")
    external = load_json_object(manifest_path)
    with tarfile.open(archive, "r:gz") as stream:
        members = stream.getmembers()
        if not members:
            raise ValueError("bundle archive is empty")
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            raise ValueError("bundle contains duplicate member names")
        for member in members:
            pure = PurePosixPath(member.name)
            if not pure.parts or pure.is_absolute() or ".." in pure.parts:
                raise ValueError(f"unsafe bundle member: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"unsupported bundle member type: {member.name}")
        roots = {PurePosixPath(member.name).parts[0] for member in members}
        if len(roots) != 1:
            raise ValueError("bundle must contain one package root")
        root = next(iter(roots))
        inner_stream = stream.extractfile(f"{root}/bundle-manifest.json")
        if inner_stream is None:
            raise ValueError("bundle internal manifest is missing")
        inner = json.loads(inner_stream.read())
        if inner != external:
            raise ValueError("bundle manifests differ")
        files = inner.get("files")
        if not isinstance(files, dict):
            raise ValueError("bundle manifest files must be an object")
        canonical_files = {_canonical_manifest_path(relative) for relative in files}
        if len(canonical_files) != len(files):
            raise ValueError("duplicate canonical bundle manifest path")
        for member in members:
            pure = PurePosixPath(member.name)
            if pure.as_posix() != member.name:
                raise ValueError(f"unsafe noncanonical bundle member: {member.name}")
        expected_members = {f"{root}/{relative}" for relative in canonical_files}
        expected_members.add(f"{root}/bundle-manifest.json")
        actual_members = {member.name for member in members if member.isfile()}
        if actual_members != expected_members:
            raise ValueError("bundle file set differs from its manifest")
        for relative, expected in files.items():
            if not isinstance(expected, str) or len(expected) != 64:
                raise ValueError(f"invalid bundle file hash: {relative}")
            member_stream = stream.extractfile(f"{root}/{relative}")
            if member_stream is None:
                raise ValueError(f"bundle file missing: {relative}")
            if hashlib.sha256(member_stream.read()).hexdigest() != expected:
                raise ValueError(f"bundle file hash mismatch: {relative}")
    return external


def _verify_extracted_package(root: Path) -> bool:
    manifest_path = root / "bundle-manifest.json"
    if not manifest_path.exists():
        return False
    manifest = load_json_object(manifest_path)
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ValueError("package manifest files must be an object")
    expected_files = {_canonical_manifest_path(relative) for relative in files}
    actual_files = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts:
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode) or (stat.S_ISREG(mode) and path.suffix == ".pyc"):
                continue
            raise ValueError(f"unsupported generated package path: {relative.as_posix()}")
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError(f"unsupported extracted package path: {relative.as_posix()}")
        if relative.as_posix() != "bundle-manifest.json":
            actual_files.add(relative.as_posix())
    if actual_files != expected_files:
        raise ValueError("extracted package file set differs from its manifest")
    for relative, expected in files.items():
        digest = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        if digest != expected:
            raise ValueError(f"extracted package hash mismatch: {relative}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed", action="store_true", help="validate installed ownership")
    parser.add_argument("--bundle", type=Path, help="validate an offline archive")
    parser.add_argument("--require-codex", action="store_true", help="require the Codex executable")
    args = parser.parse_args(argv)
    home = Path(os.environ.get("HOME") or os.environ.get("USERPROFILE") or str(Path.home()))
    try:
        lines = _check_prerequisites(home, args.require_codex)
        if _verify_extracted_package(ROOT):
            lines.append("Package manifest: valid")
        if args.installed:
            from uninstall import build_plan

            installed_plan = build_plan(home)
            if installed_plan is None:
                raise ValueError("My Codex Harness is not installed")
            if installed_plan["state"].get("cleanupIncomplete") or installed_plan["state"].get(
                "cleanupBackups"
            ):
                raise ValueError("installation is committed but cleanup remains pending")
            lines.append("Installation: healthy")
        if args.bundle is not None:
            manifest = _verify_bundle(args.bundle.absolute())
            lines.append(f"Bundle: valid ({manifest.get('version', 'unknown')})")
    except (OSError, ValueError, json.JSONDecodeError, tarfile.TarError) as error:
        print(f"doctor failed: {error}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
