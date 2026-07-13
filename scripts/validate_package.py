"""Validate the repository's distributable package contract."""

import sys
from pathlib import Path

from package_model import load_json, safe_relative_path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = (
    "README.md",
    "LICENSE",
    "NOTICE",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    ".codex-plugin/plugin.json",
    ".agents/plugins/marketplace.json",
    "scripts/package_model.py",
    "scripts/validate_package.py",
    "tests/test_package.py",
)
EXPECTED_SOURCE = {
    "source": "url",
    "url": "https://github.com/DaveBao/My_Codex_Harness.git",
    "ref": "main",
}
EXPECTED_POLICY = {
    "installation": "AVAILABLE",
    "authentication": "ON_INSTALL",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def require_nonempty_string(value: object, field: str) -> None:
    require(isinstance(value, str) and bool(value.strip()), f"{field} must be a non-empty string")


def validate_required_files() -> None:
    for relative in REQUIRED_FILES:
        path = ROOT / relative
        require(path.is_file(), f"missing required file: {relative}")
        require(path.stat().st_size > 0, f"required file is empty: {relative}")


def validate_plugin() -> None:
    manifest = load_json(ROOT / ".codex-plugin/plugin.json")
    require(manifest.get("name") == "my-codex-harness", "plugin name must be my-codex-harness")
    require(manifest.get("version") == "0.1.0", "plugin version must be 0.1.0")
    require_nonempty_string(manifest.get("description"), "plugin.description")
    author = manifest.get("author")
    require(isinstance(author, dict), "plugin.author must be an object")
    require_nonempty_string(author.get("name"), "plugin.author.name")
    require(
        manifest.get("repository") == "https://github.com/DaveBao/My_Codex_Harness",
        "plugin repository is incorrect",
    )
    require(manifest.get("license") == "MIT", "plugin license must be MIT")
    require(manifest.get("skills") == "./skills/", "plugin skills must be ./skills/")
    require(safe_relative_path(ROOT, Path(manifest["skills"])) == "skills", "plugin skills path is unsafe")

    interface = manifest.get("interface")
    require(isinstance(interface, dict), "plugin.interface must be an object")
    for field in (
        "displayName",
        "shortDescription",
        "longDescription",
        "developerName",
        "category",
    ):
        require_nonempty_string(interface.get(field), f"plugin.interface.{field}")
    require(interface["category"] == "Developer Tools", "plugin category must be Developer Tools")
    for field in ("capabilities", "defaultPrompt"):
        values = interface.get(field)
        require(
            isinstance(values, list)
            and bool(values)
            and all(isinstance(value, str) and value.strip() for value in values),
            f"plugin.interface.{field} must be a non-empty string array",
        )
    for field in ("apps", "mcpServers", "hooks"):
        require(field not in manifest, f"plugin must omit {field} without a companion file")
    for field in ("composerIcon", "logo", "logoDark", "screenshots"):
        require(field not in interface, f"plugin.interface must omit {field} without assets")


def validate_marketplace() -> None:
    marketplace = load_json(ROOT / ".agents/plugins/marketplace.json")
    require_nonempty_string(marketplace.get("name"), "marketplace.name")
    interface = marketplace.get("interface")
    require(isinstance(interface, dict), "marketplace.interface must be an object")
    require_nonempty_string(interface.get("displayName"), "marketplace.interface.displayName")
    plugins = marketplace.get("plugins")
    require(isinstance(plugins, list), "marketplace.plugins must be an array")
    entries = [entry for entry in plugins if isinstance(entry, dict) and entry.get("name") == "my-codex-harness"]
    require(len(entries) == 1, "marketplace must contain exactly one my-codex-harness entry")
    entry = entries[0]
    require(entry.get("source") == EXPECTED_SOURCE, "marketplace source is incorrect")
    require(entry.get("policy") == EXPECTED_POLICY, "marketplace policy is incorrect")
    require(entry.get("category") == "Developer Tools", "marketplace category must be Developer Tools")


def main() -> int:
    try:
        validate_required_files()
        validate_plugin()
        validate_marketplace()
    except (OSError, ValueError) as error:
        print(f"package validation failed: {error}", file=sys.stderr)
        return 1
    print("package validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
