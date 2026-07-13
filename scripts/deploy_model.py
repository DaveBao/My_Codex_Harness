"""Shared, dependency-free deployment primitives."""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path, PurePosixPath


DESIRED_AGENTS = {
    "max_depth": 1,
    "max_threads": 6,
    "interrupt_message": True,
}
EXCLUDED_PARTS = {
    ".git",
    ".worktrees",
    "__pycache__",
    ".pytest_cache",
    "cache",
    "caches",
    "dist",
    "session",
    "sessions",
}
SENSITIVE_PREFIXES = ("credential", "token")


def atomic_write_bytes(path: Path, content: bytes) -> None:
    assert_regular_or_missing(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: object) -> None:
    atomic_write_bytes(
        path,
        (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(),
    )


def load_json_object(path: Path) -> dict:
    assert_regular_file(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def assert_regular_file(path: Path) -> None:
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode):
        raise ValueError(f"refusing symlink: {path}")
    if not stat.S_ISREG(mode):
        raise ValueError(f"expected regular file: {path}")


def assert_regular_or_missing(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if stat.S_ISLNK(mode):
        raise ValueError(f"refusing symlink: {path}")
    if not stat.S_ISREG(mode):
        raise ValueError(f"expected regular file: {path}")


def assert_directory_or_missing(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if stat.S_ISLNK(mode):
        raise ValueError(f"refusing symlink directory: {path}")
    if not stat.S_ISDIR(mode):
        raise ValueError(f"expected directory: {path}")


def validate_home(home: Path) -> None:
    if not home.is_absolute():
        raise ValueError("HOME must be an absolute path")
    assert_directory_or_missing(home)


def validate_target(home: Path, path: Path, *, allow_final_symlink: bool = False) -> None:
    try:
        relative = path.relative_to(home)
    except ValueError as error:
        raise ValueError(f"target escapes HOME: {path}") from error
    if ".." in relative.parts:
        raise ValueError(f"target traversal outside HOME: {path}")
    current = home
    parts = relative.parts
    for index, part in enumerate(parts):
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        final = index == len(parts) - 1
        if stat.S_ISLNK(mode):
            if final and allow_final_symlink:
                return
            raise ValueError(f"refusing symlink in target path: {current}")
        if not final and not stat.S_ISDIR(mode):
            raise ValueError(f"expected directory in target path: {current}")
        if final and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError(f"refusing special target: {current}")


def canonical_owned_path(home: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("owned path must be a canonical POSIX relative path")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or pure.as_posix() != relative or any(part in ("", ".", "..") for part in pure.parts):
        raise ValueError("owned path must be a canonical POSIX relative path")
    path = home.joinpath(*pure.parts)
    validate_target(home, path, allow_final_symlink=True)
    return path


def is_excluded(relative: Path) -> bool:
    parts = relative.parts
    lowered = [part.lower() for part in parts]
    if any(part in EXCLUDED_PARTS for part in lowered):
        return True
    if any(part == ".env" or part.startswith(".env.") for part in lowered):
        return True
    if any(part.startswith(SENSITIVE_PREFIXES) for part in lowered):
        return True
    if any(part.startswith("secret") or Path(part).suffix in {".key", ".pem", ".p12", ".pfx"} for part in lowered):
        return True
    return relative.as_posix() == ".codex/config.toml"


def package_files(root: Path) -> list[Path]:
    files = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if is_excluded(relative):
            continue
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise ValueError(f"package contains unsupported path: {relative.as_posix()}")
        files.append(relative)
    return sorted(files, key=lambda item: item.as_posix())


def copy_package(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True)
    for relative in package_files(source):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)


def hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def hash_path(path: Path) -> str:
    mode = path.lstat().st_mode
    digest = hashlib.sha256()
    if stat.S_ISLNK(mode):
        digest.update(b"link\0")
        digest.update(os.fsencode(os.readlink(path)))
        return digest.hexdigest()
    if stat.S_ISREG(mode):
        digest.update(b"file\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    if not stat.S_ISDIR(mode):
        raise ValueError(f"cannot hash special path: {path}")
    return _hash_tree(path, _all_tree_entries(path))


def hash_package(path: Path) -> str:
    files = package_files(path)
    directories = {parent for relative in files for parent in relative.parents if parent != Path(".")}
    entries = [(relative, "directory") for relative in directories]
    entries.extend((relative, "file") for relative in files)
    return _hash_tree(path, entries)


def _all_tree_entries(root: Path) -> list[tuple[Path, str]]:
    entries = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            kind = "directory"
        elif stat.S_ISREG(mode):
            kind = "file"
        elif stat.S_ISLNK(mode):
            kind = "link"
        else:
            raise ValueError(f"cannot hash special path: {path}")
        entries.append((relative, kind))
    return entries


def _hash_tree(root: Path, entries: list[tuple[Path, str]]) -> str:
    digest = hashlib.sha256()
    digest.update(b"tree\0")
    for relative, kind in sorted(entries, key=lambda item: item[0].as_posix()):
        digest.update(relative.as_posix().encode())
        digest.update(b"\0")
        digest.update(kind.encode())
        digest.update(b"\0")
        if kind == "file":
            digest.update(hash_path(root / relative).encode())
            digest.update(b"\0")
        elif kind == "link":
            digest.update(os.fsencode(os.readlink(root / relative)))
            digest.update(b"\0")
    return digest.hexdigest()


def source_commit(root: Path) -> str:
    bundle_manifest = root / "bundle-manifest.json"
    if bundle_manifest.is_file() and not bundle_manifest.is_symlink():
        value = load_json_object(bundle_manifest).get("sourceCommit")
        if isinstance(value, str) and value:
            return value
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def package_version(root: Path) -> str:
    value = load_json_object(root / ".codex-plugin/plugin.json").get("version")
    if not isinstance(value, str) or not value:
        raise ValueError("plugin version must be a non-empty string")
    return value


def remove_path(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if stat.S_ISDIR(mode) and not stat.S_ISLNK(mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def home_relative(home: Path, path: Path) -> str:
    return path.relative_to(home).as_posix()
