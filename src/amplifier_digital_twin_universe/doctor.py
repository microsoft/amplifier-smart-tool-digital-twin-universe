"""Diagnosis: evidence first, then a reading of it.

Model-backed. Consumes tokens and may return a different answer on a second run.

The evidence is gathered deterministically before the model sees anything, and it
is returned alongside the diagnosis so a caller can check the reading against what
was actually measured. A probe that fails becomes evidence of a failure rather
than an aborted diagnosis. Nothing here repairs anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from amplifier_digital_twin_universe import environments
from amplifier_digital_twin_universe.drafting import DEFAULT_MAX_ATTEMPTS, draft_json, repair_section
from amplifier_digital_twin_universe.errors import SmartToolError
from amplifier_digital_twin_universe.intelligence import Intelligence, resolve
from amplifier_digital_twin_universe.knowledge import troubleshooting_guide
from amplifier_digital_twin_universe.prerequisites import HostReport, probe

CONFIDENCE_LEVELS = ("high", "medium", "low")

__all__ = ["Diagnosis", "Finding", "diagnose", "gather_evidence"]


@dataclass(frozen=True)
class Finding:
    """One thing that is wrong, why it is wrong, and what fixes it."""

    issue: str
    cause: str
    confidence: str
    remedy: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue": self.issue,
            "cause": self.cause,
            "confidence": self.confidence,
            "remedy": list(self.remedy),
            "commands": list(self.commands),
        }


@dataclass(frozen=True)
class Diagnosis:
    """A reading of the evidence, with the evidence attached."""

    summary: str
    findings: list[Finding] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "findings": [finding.to_dict() for finding in self.findings],
            "evidence": dict(self.evidence),
            "usage": dict(self.usage),
        }


def gather_evidence(environment_id: str | None = None, *, host: HostReport | None = None) -> dict[str, Any]:
    """Measure the host, and one environment when named.

    Deterministic. Requires no model provider. A probe that fails is recorded as
    a failure rather than raised, because a failed probe is itself a symptom.
    """
    report = host if host is not None else probe()
    evidence: dict[str, Any] = {"host": report.to_dict()}

    if not report.can_launch:
        return evidence

    evidence["environments"] = _measure(environments.list_environments)
    if environment_id:
        evidence["environment"] = {
            "id": environment_id,
            "status": _measure(environments.status, environment_id),
            "readiness": _measure(environments.check_readiness, environment_id, skip_access_check=True),
        }
    return evidence


def diagnose(
    symptom: str | None = None,
    *,
    environment_id: str | None = None,
    context: str | None = None,
    evidence: dict[str, Any] | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    provider: str | None = None,
    model: str | None = None,
    intelligence: Intelligence | None = None,
) -> Diagnosis:
    """Explain what is wrong with this host or one environment, and how to fix it.

    Model-backed. Consumes tokens and may return a different answer on a second
    run.

    `symptom` is what the caller observed, in their own words. Without it the
    diagnosis covers whatever the evidence itself shows.
    """
    engine = resolve(intelligence)
    engine.preflight(provider)
    measured = evidence if evidence is not None else gather_evidence(environment_id)

    drafted = draft_json(
        engine,
        lambda findings: _build_prompt(measured, symptom, context, findings),
        _check,
        what="diagnosis",
        max_attempts=max_attempts,
        provider=provider,
        model=model,
    )

    document = drafted.document
    return Diagnosis(
        summary=str(document.get("summary", "")).strip(),
        findings=[
            Finding(
                issue=str(item.get("issue", "")).strip(),
                cause=str(item.get("cause", "")).strip(),
                confidence=str(item.get("confidence", "low")).strip().lower(),
                remedy=[str(step) for step in item.get("remedy", [])],
                commands=[str(command) for command in item.get("commands", [])],
            )
            for item in document.get("findings", [])
        ],
        evidence=measured,
        usage=drafted.usage(),
    )


def _measure(operation: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return operation(*args, **kwargs)
    except SmartToolError as exc:
        return {"error": {"code": exc.code, "message": exc.message}}
    except Exception as exc:
        return {"error": {"code": "failed", "message": str(exc)}}


def _check(document: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    if not str(document.get("summary", "")).strip():
        findings.append("'summary' is missing or empty")

    items = document.get("findings")
    if not isinstance(items, list):
        findings.append("'findings' is missing or is not a list")
        return findings

    for index, item in enumerate(items):
        where = f"findings[{index}]"
        if not isinstance(item, dict):
            findings.append(f"{where} is not an object")
            continue
        for key in ("issue", "cause"):
            if not str(item.get(key, "")).strip():
                findings.append(f"{where}.{key} is missing or empty")
        confidence = str(item.get("confidence", "")).strip().lower()
        if confidence not in CONFIDENCE_LEVELS:
            findings.append(f"{where}.confidence must be one of {', '.join(CONFIDENCE_LEVELS)}")
        remedy = item.get("remedy", [])
        if not isinstance(remedy, list) or not remedy:
            findings.append(f"{where}.remedy must be a non-empty list of steps")
    return findings


def _build_prompt(
    evidence: dict[str, Any],
    symptom: str | None,
    context: str | None,
    findings: list[str],
) -> str:
    parts = [
        troubleshooting_guide(),
        "",
        "# Task",
        "Diagnose this host and, when one is named, this environment.",
        "Ground every finding in the evidence below. Do not invent a measurement that is not there.",
        "When the evidence does not support a cause, say so and lower the confidence.",
        "",
        "## Evidence",
        "```json",
        json.dumps(evidence, indent=2, sort_keys=True, default=str),
        "```",
    ]

    if symptom:
        parts += ["", "## What the caller observed", symptom.strip()]
    else:
        parts += [
            "",
            "## What the caller observed",
            (
                "Nothing specific. Report what the evidence itself shows, and return an empty "
                "findings list when it shows nothing wrong."
            ),
        ]

    if context:
        parts += ["", "## Additional context supplied by the caller", context.strip()]

    parts += [
        "",
        "## Answer format",
        "Reply with one JSON object in a ```json fenced block and nothing after it:",
        "```json",
        json.dumps(
            {
                "summary": "one sentence naming the most likely problem",
                "findings": [
                    {
                        "issue": "what is wrong, in the caller's terms",
                        "cause": "why it is happening, tied to the evidence",
                        "confidence": "high",
                        "remedy": ["what to do, in order"],
                        "commands": ["shell command that carries out the remedy"],
                    }
                ],
            },
            indent=2,
        ),
        "```",
        f"confidence is one of: {', '.join(CONFIDENCE_LEVELS)}.",
        *repair_section(findings),
    ]
    return "\n".join(parts)
