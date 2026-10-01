from importlib.metadata import version
import json
from pathlib import Path

import yaml

from digital_twin_universe.core.manifest import MANIFEST_PATH
from digital_twin_universe.lib import load_manifest

DISTRIBUTION_ROOT = Path(__file__).parents[1]
SKILL_PATH = DISTRIBUTION_ROOT / "skills" / "digital-twin-universe" / "SKILL.md"


def test_manifest_matches_the_descriptor_and_package() -> None:
    manifest = load_manifest()
    descriptor = json.loads((DISTRIBUTION_ROOT / "smart-tool.json").read_text(encoding="utf-8"))

    assert DISTRIBUTION_ROOT / descriptor["manifest"] == MANIFEST_PATH
    assert manifest.name == descriptor["cli_argv"][0]
    assert manifest.version == version("digital-twin-universe")


def test_skill_carries_the_package_version() -> None:
    _, frontmatter, _ = SKILL_PATH.read_text(encoding="utf-8").split("---", 2)

    assert yaml.safe_load(frontmatter)["metadata"]["version"] == version("digital-twin-universe")
