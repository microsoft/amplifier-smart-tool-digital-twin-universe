"""Draft, check, repair.

The model proposes and deterministic code decides. Every model-backed capability
that wants a structured answer runs through here: the reply is parsed, checked by
code the model cannot influence, and handed back with the findings until it passes
or the budget is spent.

A capability supplies two functions: one that builds a prompt from optional repair
feedback, and one that returns the findings against a parsed document. Empty
findings means accepted.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from amplifier_digital_twin_universe.errors import GenerationFailedError
from amplifier_digital_twin_universe.intelligence import Intelligence, ModelRequest
from amplifier_digital_twin_universe.parsing import extract_json_object

DEFAULT_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Drafted:
    """An accepted document, and what it cost to get there."""

    document: dict[str, Any]
    attempts: int
    provider: str = ""
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def usage(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "attempts": self.attempts,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": None if self.cost_usd is None else str(self.cost_usd),
        }


def draft_json(
    engine: Intelligence,
    build_prompt: Callable[[list[str]], str],
    check: Callable[[dict[str, Any]], list[str]],
    *,
    what: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    provider: str | None = None,
    model: str | None = None,
) -> Drafted:
    """Run the draft-and-repair loop until a document passes `check`."""
    history: list[dict[str, Any]] = []
    tokens_in = tokens_out = 0
    cost: Decimal | None = None
    used_provider = ""
    used_model: str | None = None
    findings: list[str] = []

    for attempt in range(1, max_attempts + 1):
        turn = engine.run(ModelRequest(prompt=build_prompt(findings), provider=provider, model=model))
        tokens_in += turn.tokens_in
        tokens_out += turn.tokens_out
        cost = turn.cost_usd if cost is None else cost + (turn.cost_usd or Decimal(0))
        used_provider = turn.provider
        used_model = turn.model

        document = extract_json_object(turn.text)
        findings = ["the reply contained no JSON object"] if document is None else check(document)

        history.append({"attempt": attempt, "accepted": not findings, "findings": list(findings)})

        if document is not None and not findings:
            return Drafted(
                document=document,
                attempts=attempt,
                provider=used_provider,
                model=used_model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
                history=history,
            )

    raise GenerationFailedError(
        f"No usable {what} after {max_attempts} attempt(s).",
        "Rerun with a more specific request, or raise the attempt budget with --max-attempts. "
        "The per-attempt findings are in this error's attempts field.",
        attempts=history,
    )


def repair_section(findings: list[str]) -> list[str]:
    """The prompt section that reports a rejected attempt, or nothing on the first."""
    if not findings:
        return []
    return [
        "",
        "## Your previous answer was rejected",
        *[f"- {finding}" for finding in findings],
        "",
        "Emit a corrected answer. Fix every finding above.",
    ]
