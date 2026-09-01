"""Profile creation: the model drafts, deterministic code decides.

The model never decides whether its own output is good. Every draft is run
through the engine's own loader, and the loader's findings are handed back for
repair. What a caller receives is a profile that parses, or a failure that names
every attempt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from amplifier_digital_twin_universe.errors import GenerationFailedError
from amplifier_digital_twin_universe.intelligence import Intelligence, ModelRequest, resolve
from amplifier_digital_twin_universe.knowledge import authoring_guide, example_profiles
from amplifier_digital_twin_universe.parsing import extract_yaml
from amplifier_digital_twin_universe.profiles import ValidationReport, validate_profile

DEFAULT_MAX_ATTEMPTS = 3

__all__ = ["DEFAULT_MAX_ATTEMPTS", "ProfileDraft", "create_profile", "extract_yaml"]


@dataclass(frozen=True)
class ProfileDraft:
    """A profile the loader accepts, plus what it cost to get there."""

    yaml_text: str
    name: str | None
    description: str | None
    attempts: int
    warnings: list[str] = field(default_factory=list)
    unresolved_variables: list[str] = field(default_factory=list)
    provider: str = ""
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "yaml": self.yaml_text,
            "name": self.name,
            "description": self.description,
            "attempts": self.attempts,
            "warnings": list(self.warnings),
            "unresolved_variables": list(self.unresolved_variables),
            "usage": {
                "provider": self.provider,
                "model": self.model,
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
                "cost_usd": None if self.cost_usd is None else str(self.cost_usd),
            },
        }


def create_profile(
    description: str,
    *,
    context: str | None = None,
    variables: dict[str, str] | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    provider: str | None = None,
    model: str | None = None,
    intelligence: Intelligence | None = None,
) -> ProfileDraft:
    """Draft a launchable DTU profile from a description of what to test.

    Model-backed. Consumes tokens and may return a different answer on a second
    run.

    `context` is additional material the caller already holds that would help,
    passed as data rather than as a path. A CLI may read a file into it; the
    library takes the content.
    """
    if not description.strip():
        raise ValueError("description must not be empty")

    engine = resolve(intelligence)
    engine.preflight(provider)
    variables = dict(variables or {})

    history: list[dict[str, Any]] = []
    tokens_in = tokens_out = 0
    cost: Decimal | None = None
    used_provider = ""
    used_model: str | None = None

    previous_yaml: str | None = None
    previous_report: ValidationReport | None = None

    for attempt in range(1, max_attempts + 1):
        prompt = _build_prompt(
            description=description,
            context=context,
            variables=variables,
            previous_yaml=previous_yaml,
            previous_report=previous_report,
        )
        turn = engine.run(ModelRequest(prompt=prompt, provider=provider, model=model))

        tokens_in += turn.tokens_in
        tokens_out += turn.tokens_out
        cost = _add_cost(cost, turn.cost_usd)
        used_provider = turn.provider
        used_model = turn.model

        yaml_text = extract_yaml(turn.text)
        if yaml_text is None:
            report = ValidationReport(valid=False, errors=["the reply contained no ```yaml fenced block"])
        else:
            report = validate_profile(yaml_text, variables)

        history.append(
            {
                "attempt": attempt,
                "valid": report.valid,
                "errors": list(report.errors),
                "warnings": list(report.warnings),
            }
        )

        # A profile that parses but drops fields silently is a profile that
        # launches and does the wrong thing, so warnings block acceptance too.
        if report.valid and not report.warnings:
            return ProfileDraft(
                yaml_text=yaml_text or "",
                name=report.name,
                description=report.description,
                attempts=attempt,
                warnings=list(report.warnings),
                unresolved_variables=list(report.unresolved_variables),
                provider=used_provider,
                model=used_model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
            )

        previous_yaml = yaml_text
        previous_report = report

    raise GenerationFailedError(
        f"No launchable profile after {max_attempts} attempt(s).",
        "Rerun with a more specific description, or raise the attempt budget with "
        "--max-attempts. The per-attempt findings are in this error's attempts field.",
        attempts=history,
    )


def _build_prompt(
    *,
    description: str,
    context: str | None,
    variables: dict[str, str],
    previous_yaml: str | None,
    previous_report: ValidationReport | None,
) -> str:
    parts = [
        authoring_guide(),
        "",
        "# Worked examples",
        "Profiles that launch today. Follow their shape.",
    ]

    for name, text in example_profiles().items():
        parts += ["", f"## {name}", "```yaml", text.strip(), "```"]

    parts += [
        "",
        "# Task",
        "Write one Digital Twin Universe profile for the following request.",
        "Reply with the profile in a single ```yaml fenced block and nothing after it.",
        "",
        "## Request",
        description.strip(),
    ]

    if variables:
        names = ", ".join(sorted(variables))
        parts += [
            "",
            "## Variables supplied at launch",
            f"These names resolve at launch and may be referenced as ${{NAME}}: {names}.",
            "Do not reference any other variable name.",
        ]
    else:
        parts += [
            "",
            "## Variables supplied at launch",
            "None. Do not reference any ${VAR} in the profile.",
        ]

    if context:
        parts += ["", "## Additional context supplied by the caller", context.strip()]

    if previous_yaml is not None and previous_report is not None:
        parts += [
            "",
            "## Your previous attempt was rejected",
            "```yaml",
            previous_yaml.strip(),
            "```",
            "",
            previous_report.feedback(),
            "",
            "Emit a corrected profile. Fix every finding above.",
        ]

    return "\n".join(parts)


def _add_cost(total: Decimal | None, turn: Decimal | None) -> Decimal | None:
    if turn is None:
        return total
    return turn if total is None else total + turn
