"""Small, dependency-free helpers shared by package scripts."""

import hashlib as _hashlib
import json as _json
import math as _math
import os as _os
import re as _re
import tempfile as _tempfile
from pathlib import Path

try:
    import tomllib as _tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility
    _tomllib = None


__all__ = [
    "sha256_file",
    "safe_relative_path",
    "load_json",
    "atomic_write",
    "parse_agents_table",
    "merge_agents_table",
]

_BARE_KEY = _re.compile(r"^[A-Za-z0-9_-]+$")
_TABLE = _re.compile(r"^\[([^\[\]]+)\]$")


def sha256_file(path: Path) -> str:
    digest = _hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative_path(root: Path, candidate: Path) -> str:
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        return resolved_candidate.relative_to(resolved_root).as_posix()
    except ValueError as error:
        raise ValueError(f"path escapes root: {candidate}") from error


def load_json(path: Path) -> dict:
    value = _json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with _tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            _os.fsync(stream.fileno())
        _os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def parse_agents_table(config_text: str) -> dict[str, object]:
    if _tomllib is not None:
        agents = _tomllib.loads(config_text).get("agents", {})
        if not isinstance(agents, dict):
            raise ValueError("[agents] must be a table")
        return agents

    agents: dict[str, object] = {}
    in_agents = False
    for raw_line in config_text.splitlines():
        line = _strip_comment(raw_line).strip()
        if not line:
            continue
        table = _table_name(line)
        if table is not None:
            if table == "agents":
                in_agents = True
                continue
            if in_agents:
                break
            continue
        if in_agents:
            key, value = _split_once(line, "=")
            agents[_parse_key(key.strip())] = _parse_value(value.strip())
    return agents


def merge_agents_table(
    config_text: str, desired: dict[str, object]
) -> tuple[str, dict[str, object]]:
    existing = parse_agents_table(config_text)
    additions = {key: value for key, value in desired.items() if key not in existing}
    merged = {**existing, **additions}
    if not additions:
        return config_text, merged

    entries = "".join(
        f"{_format_key(key)} = {_format_value(value)}\n"
        for key, value in sorted(additions.items())
    )
    lines = config_text.splitlines(keepends=True)
    header_index = next(
        (index for index, line in enumerate(lines) if _table_name(_strip_comment(line).strip()) == "agents"),
        None,
    )
    if header_index is None:
        separator = "" if not config_text else ("\n" if config_text.endswith("\n") else "\n\n")
        return f"{config_text}{separator}[agents]\n{entries}", merged

    insertion_index = len(lines)
    for index in range(header_index + 1, len(lines)):
        if _table_name(_strip_comment(lines[index]).strip()) is not None:
            insertion_index = index
            break
    while insertion_index > header_index + 1 and not lines[insertion_index - 1].strip():
        insertion_index -= 1
    prefix = ""
    if insertion_index and not lines[insertion_index - 1].endswith(("\n", "\r")):
        prefix = "\n"
    lines.insert(insertion_index, prefix + entries)
    return "".join(lines), merged


def _strip_comment(line: str) -> str:
    quote = None
    escaped = False
    for index, character in enumerate(line):
        if quote == '"' and character == "\\" and not escaped:
            escaped = True
            continue
        if character in ("'", '"') and not escaped:
            quote = None if quote == character else (character if quote is None else quote)
        elif character == "#" and quote is None:
            return line[:index]
        escaped = False
    return line


def _table_name(line: str) -> str | None:
    match = _TABLE.fullmatch(line)
    return match.group(1).strip() if match else None


def _split_once(text: str, separator: str) -> tuple[str, str]:
    quote = None
    depth = 0
    escaped = False
    for index, character in enumerate(text):
        if quote == '"' and character == "\\" and not escaped:
            escaped = True
            continue
        if character in ("'", '"') and not escaped:
            quote = None if quote == character else (character if quote is None else quote)
        elif quote is None:
            if character in "[{":
                depth += 1
            elif character in "]}":
                depth -= 1
            elif character == separator and depth == 0:
                return text[:index], text[index + 1 :]
        escaped = False
    raise ValueError(f"expected {separator!r} in {text!r}")


def _split_items(text: str) -> list[str]:
    items = []
    remainder = text.strip()
    while remainder:
        try:
            item, remainder = _split_once(remainder, ",")
        except ValueError:
            item, remainder = remainder, ""
        if item.strip():
            items.append(item.strip())
        remainder = remainder.strip()
    return items


def _parse_key(key: str) -> str:
    if _BARE_KEY.fullmatch(key):
        return key
    value = _parse_value(key)
    if not isinstance(value, str):
        raise ValueError(f"invalid TOML key: {key}")
    return value


def _parse_value(value: str) -> object:
    if value.startswith('"'):
        return _json.loads(value)
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    if value == "true":
        return True
    if value == "false":
        return False
    if value.startswith("[") and value.endswith("]"):
        return [_parse_value(item) for item in _split_items(value[1:-1])]
    if value.startswith("{") and value.endswith("}"):
        return {
            _parse_key(key.strip()): _parse_value(item.strip())
            for key, item in (_split_once(entry, "=") for entry in _split_items(value[1:-1]))
        }
    number = value.replace("_", "")
    try:
        return int(number)
    except ValueError:
        try:
            return float(number)
        except ValueError as error:
            raise ValueError(f"unsupported TOML value: {value}") from error


def _format_key(key: str) -> str:
    if not isinstance(key, str):
        raise ValueError("agent names must be strings")
    return key if _BARE_KEY.fullmatch(key) else _json.dumps(key, ensure_ascii=False)


def _format_value(value: object) -> str:
    if isinstance(value, str):
        return _json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and _math.isfinite(value):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_format_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(
            f"{_format_key(key)} = {_format_value(item)}" for key, item in sorted(value.items())
        ) + " }"
    raise ValueError(f"unsupported agent value: {value!r}")
