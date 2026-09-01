"""Access to the smart tool's own manifest.

The spec requires the manifest to be reachable through the library rather than by
locating a file, because install layouts differ by ecosystem and no filesystem
path is portable across them. `load_manifest()` is that accessor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import importlib.resources as resources
from pathlib import Path
import tomllib
from typing import Any

MANIFEST_FILENAME = "SMART_TOOL.md"


class ManifestError(RuntimeError):
    """The manifest could not be located or parsed."""


@dataclass(frozen=True)
class Requirement:
    """One entry from the manifest's `requires` list."""

    name: str
    purpose: str
    install: str
    optional: bool = False


@dataclass(frozen=True)
class Manifest:
    """The structured form of `SMART_TOOL.md`."""

    smart_tool_format: int
    name: str
    version: str
    description: str
    use_cases: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    requires: list[Requirement] = field(default_factory=list)
    body: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the manifest as plain data, suitable for JSON emission."""
        return {
            "smart_tool_format": self.smart_tool_format,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "use_cases": list(self.use_cases),
            "platforms": list(self.platforms),
            "requires": [
                {
                    "name": r.name,
                    "purpose": r.purpose,
                    "install": r.install,
                    "optional": r.optional,
                }
                for r in self.requires
            ],
        }


def load_manifest() -> Manifest:
    """Read the manifest shipped with this tool.

    Deterministic. Requires no model provider.
    """
    return _parse(_read_manifest_text())


def _read_manifest_text() -> str:
    """Read the manifest from the copy that ships inside the package."""
    packaged = resources.files("amplifier_digital_twin_universe").joinpath(MANIFEST_FILENAME)
    if not packaged.is_file():
        raise ManifestError(f"{MANIFEST_FILENAME} was not found inside the installed package.")
    return packaged.read_text(encoding="utf-8")


def _parse(text: str) -> Manifest:
    """Split frontmatter from body and build a Manifest."""
    front, body = _split_frontmatter(text)
    data = _parse_yaml_subset(front)

    missing = [
        key
        for key in ("smart_tool_format", "name", "version", "description", "use_cases", "platforms")
        if key not in data
    ]
    if missing:
        raise ManifestError(f"{MANIFEST_FILENAME} is missing required fields: {', '.join(missing)}")

    requires = [
        Requirement(
            name=str(entry["name"]),
            purpose=str(entry["purpose"]),
            install=str(entry["install"]),
            optional=bool(entry.get("optional", False)),
        )
        for entry in data.get("requires", [])
    ]

    return Manifest(
        smart_tool_format=int(data["smart_tool_format"]),
        name=str(data["name"]),
        version=str(data["version"]),
        description=str(data["description"]).strip(),
        use_cases=[str(v) for v in data.get("use_cases", [])],
        platforms=[str(v) for v in data.get("platforms", [])],
        requires=requires,
        body=body.strip(),
    )


def _split_frontmatter(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ManifestError(f"{MANIFEST_FILENAME} does not open with a '---' frontmatter fence.")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    raise ManifestError(f"{MANIFEST_FILENAME} frontmatter fence is never closed.")


def _parse_yaml_subset(front: str) -> dict[str, Any]:
    """Parse the YAML subset the manifest is permitted to use.

    Supported: top-level scalars, folded block scalars (`>`), block sequences of
    scalars, and block sequences of mappings. Deliberately narrow -- the upstream
    conformance kit parses the same subset, so anything richer would pass here and
    fail there.
    """
    data: dict[str, Any] = {}
    lines = front.splitlines()
    index = 0

    while index < len(lines):
        raw = lines[index]
        if not raw.strip() or raw.lstrip().startswith("#"):
            index += 1
            continue
        if raw[:1].isspace() or raw.lstrip().startswith("- "):
            raise ManifestError(f"Unexpected indentation in frontmatter: {raw!r}")

        key, _, inline = raw.partition(":")
        key = key.strip()
        inline = inline.strip()
        index += 1

        if inline in (">", "|", ">-", "|-"):
            block, index = _consume_indented(lines, index)
            joiner = "\n" if inline.startswith("|") else " "
            data[key] = joiner.join(s.strip() for s in block).strip()
        elif inline:
            data[key] = _scalar(inline)
        else:
            block, index = _consume_indented(lines, index)
            data[key] = _parse_sequence(block)

    return data


def _consume_indented(lines: list[str], index: int) -> tuple[list[str], int]:
    block: list[str] = []
    while index < len(lines):
        line = lines[index]
        if line.strip() and not line[:1].isspace():
            break
        block.append(line)
        index += 1
    while block and not block[-1].strip():
        block.pop()
    return block, index


def _parse_sequence(block: list[str]) -> list[Any]:
    items: list[Any] = []
    for line in block:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            rest = stripped[2:].strip()
            if ":" in rest and not rest.endswith(":"):
                key, _, value = rest.partition(":")
                items.append({key.strip(): _scalar(value.strip())})
            else:
                items.append(_scalar(rest))
        elif items and isinstance(items[-1], dict):
            key, _, value = stripped.partition(":")
            items[-1][key.strip()] = _scalar(value.strip())
        else:
            raise ManifestError(f"Unexpected sequence line in frontmatter: {line!r}")
    return items


def _scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if value.isdigit():
        return int(value)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def package_version() -> str:
    """Read the version from the package definition, for consistency checks."""
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if not pyproject.is_file():
        raise ManifestError("pyproject.toml was not found; not running from a source checkout.")
    with pyproject.open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])
