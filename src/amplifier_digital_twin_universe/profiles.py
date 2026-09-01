"""Profile validation.

Deterministic. Runs with no model provider configured.

Validation delegates to the DTU engine's own loader, so a profile that validates
here parses identically at launch. Reimplementing the schema would produce a
second answer to a question that has one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import warnings


@dataclass(frozen=True)
class ValidationReport:
    """The outcome of checking one profile document."""

    valid: bool
    name: str | None = None
    description: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unresolved_variables: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "name": self.name,
            "description": self.description,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "unresolved_variables": list(self.unresolved_variables),
        }

    def feedback(self) -> str:
        """Render the findings as repair instructions for a model."""
        lines: list[str] = []
        if self.errors:
            lines.append("Errors that must be fixed:")
            lines.extend(f"- {e}" for e in self.errors)
        if self.warnings:
            lines.append("Warnings that must be fixed:")
            lines.extend(f"- {w}" for w in self.warnings)
        if self.unresolved_variables:
            names = ", ".join(sorted(self.unresolved_variables))
            lines.append(
                f"Unresolved variables remain after substitution: {names}. "
                "Either remove the reference or use a variable the caller supplies."
            )
        return "\n".join(lines)


def validate_profile(
    yaml_text: str,
    variables: dict[str, str] | None = None,
    base_dir: Path | str | None = None,
) -> ValidationReport:
    """Check whether a profile document is launchable.

    Deterministic. Requires no model provider.

    A profile is launchable when the engine's loader accepts it and every host
    path it names exists. Warnings are reported alongside errors because the
    loader drops unknown fields silently, and a silently dropped field is a
    profile that launches and does the wrong thing.

    `base_dir` is the directory relative `provision.files` sources resolve
    against, matching how the engine resolves them against the profile's own
    location. Without it, relative sources resolve against the process cwd.
    """
    from amplifier_bundle_digital_twin_universe import profile as dtu_profile
    import yaml

    variables = dict(variables or {})

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            loaded = dtu_profile.load_profile_from_content(yaml_text, variables, validate=True)
        except KeyError as exc:
            # The loader subscripts required keys directly, so an absent one
            # surfaces as a bare KeyError with no context of its own.
            return ValidationReport(
                valid=False,
                errors=[f"missing required key {exc} in a profile entry"],
                warnings=[str(w.message) for w in caught],
            )
        except yaml.YAMLError as exc:
            return ValidationReport(
                valid=False,
                errors=[f"the document is not valid YAML: {exc}"],
                warnings=[str(w.message) for w in caught],
            )
        except (ValueError, TypeError) as exc:
            return ValidationReport(
                valid=False,
                errors=[str(exc)],
                warnings=[str(w.message) for w in caught],
            )

        warning_texts = [str(w.message) for w in caught]

    # The loader accepts a provision.files source that does not exist; the launch
    # is where it fails. Catching it here is the difference between "parses" and
    # "launches", which is what a caller asked about.
    missing = _missing_sources(loaded, base_dir)

    return ValidationReport(
        valid=not missing,
        name=loaded.name,
        description=loaded.description,
        errors=missing,
        warnings=warning_texts,
        unresolved_variables=_unresolved(yaml_text, variables),
    )


def _missing_sources(loaded: Any, base_dir: Path | str | None) -> list[str]:
    """provision.files sources that will not resolve at launch."""
    provision = getattr(loaded, "provision", None)
    if provision is None:
        return []

    root = Path(base_dir) if base_dir is not None else Path.cwd()
    missing: list[str] = []
    for entry in getattr(provision, "files", []) or []:
        src = str(entry.src)
        if "${" in src:
            # Still unresolved; reported separately as an unresolved variable.
            continue
        candidate = Path(src)
        if not candidate.is_absolute():
            candidate = root / candidate
        if not candidate.exists():
            missing.append(
                f"provision.files source {src!r} does not exist (resolved to "
                f"{candidate}). Push only files that exist on the host; write "
                f"generated content with a heredoc in setup_cmds instead."
            )
        elif candidate.is_dir() and not entry.recursive:
            missing.append(f"provision.files source {src!r} is a directory but 'recursive' is not set.")
    return missing


def _unresolved(yaml_text: str, variables: dict[str, str]) -> list[str]:
    """Names still referenced as ${NAME} after substitution.

    An unresolved reference is not always an error: the engine deliberately skips
    the rewrite proxy when a rule target still carries one. It is always worth
    reporting, because the same reference in an integer field fails the launch.
    """
    import re

    found = {m.group(1) for m in re.finditer(r"\$\{([^}]+)\}", yaml_text)}
    return sorted(found - set(variables))
