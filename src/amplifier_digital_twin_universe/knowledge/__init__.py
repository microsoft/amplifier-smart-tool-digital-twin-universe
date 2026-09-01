"""The bottled Digital Twin Universe expertise.

Every asset ships inside the package and is read through an accessor rather than
by locating a file, for the same reason the manifest is: install layouts differ by
ecosystem and no filesystem path is portable across them.

Deterministic. Everything here reads files and requires no model provider.
"""

from __future__ import annotations

from functools import lru_cache
import importlib.resources as resources

AUTHORING_GUIDE = "profile-authoring.md"
INSTALL_GUIDE = "installing.md"
TROUBLESHOOTING_GUIDE = "troubleshooting.md"
EXAMPLES_PACKAGE = "amplifier_digital_twin_universe.knowledge.examples"

__all__ = [
    "authoring_guide",
    "example_profiles",
    "install_guide",
    "troubleshooting_guide",
]


@lru_cache(maxsize=1)
def authoring_guide() -> str:
    """The profile schema and the rules for writing one that launches."""
    return _read(AUTHORING_GUIDE)


@lru_cache(maxsize=1)
def install_guide() -> str:
    """What each prerequisite is for, and how to install it per platform."""
    return _read(INSTALL_GUIDE)


@lru_cache(maxsize=1)
def troubleshooting_guide() -> str:
    """Known symptoms, their causes, and the command that repairs each."""
    return _read(TROUBLESHOOTING_GUIDE)


@lru_cache(maxsize=1)
def example_profiles() -> dict[str, str]:
    """Profiles that launch, keyed by name, ordered by name."""
    directory = resources.files(EXAMPLES_PACKAGE)
    return {
        entry.name.removesuffix(".yaml"): entry.read_text(encoding="utf-8")
        for entry in sorted(directory.iterdir(), key=lambda item: item.name)
        if entry.name.endswith(".yaml")
    }


def _read(filename: str) -> str:
    return resources.files(__name__).joinpath(filename).read_text(encoding="utf-8")
