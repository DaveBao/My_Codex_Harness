#!/usr/bin/env python3
"""Build a reproducible, self-contained offline Harness archive."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deploy_model import (  # noqa: E402
    DESIRED_AGENTS,
    atomic_write_bytes,
    atomic_write_json,
    package_files,
    package_version,
    source_commit,
)


ROOT = Path(__file__).resolve().parents[1]


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_bundle(package_root: Path, output: Path) -> tuple[Path, Path, Path]:
    package_root = package_root.absolute()
    output = output.absolute()
    try:
        output_relative = output.relative_to(package_root)
    except ValueError:
        output_relative = None
    if output_relative is not None and output_relative.parts and output_relative.parts[0] != "dist":
        raise ValueError("bundle output inside the package must use dist/")
    version = package_version(package_root)
    root_name = f"my-codex-harness-{version}"
    archive = output / f"{root_name}.tar.gz"
    sidecar = output / f"{root_name}.tar.gz.sha256"
    manifest_path = output / "bundle-manifest.json"
    files = package_files(package_root)
    manifest = {
        "version": version,
        "sourceCommit": source_commit(package_root),
        "desiredAgents": dict(DESIRED_AGENTS),
        "files": {relative.as_posix(): _file_hash(package_root / relative) for relative in files},
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    entries = [(f"{root_name}/{relative.as_posix()}", package_root / relative) for relative in files]
    entries.append((f"{root_name}/bundle-manifest.json", manifest_bytes))
    entries.sort(key=lambda item: item[0])

    output.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as stream:
            for name, source in entries:
                data = source if isinstance(source, bytes) else source.read_bytes()
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mtime = 0
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                info.mode = 0o755 if name.endswith(("bootstrap.sh", ".py")) else 0o644
                stream.addfile(info, io.BytesIO(data))
    atomic_write_bytes(archive, buffer.getvalue())
    digest = hashlib.sha256(buffer.getvalue()).hexdigest()
    atomic_write_bytes(sidecar, f"{digest}  {archive.name}\n".encode())
    atomic_write_json(manifest_path, manifest)
    return archive, sidecar, manifest_path


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < (3, 11):
        print("bundle build failed: Python 3.11 or newer is required", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        archive, sidecar, manifest = build_bundle(ROOT, args.output)
    except (OSError, ValueError, json.JSONDecodeError, tarfile.TarError) as error:
        print(f"bundle build failed: {error}", file=sys.stderr)
        return 1
    print(archive)
    print(sidecar)
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
