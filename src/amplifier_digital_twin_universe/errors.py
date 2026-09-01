"""Failure types.

Failures are loud and they name the remedy. Every error carries a stable `code`
a caller can branch on and a `remedy` a caller can act on, so nothing has to be
inferred from prose or from an empty result.
"""

from __future__ import annotations


class SmartToolError(Exception):
    """Base for every failure this tool raises deliberately."""

    code = "failed"

    def __init__(self, message: str, remedy: str) -> None:
        super().__init__(message)
        self.message = message
        self.remedy = remedy


class MissingPrerequisiteError(SmartToolError):
    """Something the manifest declares under `requires` is absent."""

    code = "missing_prerequisite"


class NoProviderError(SmartToolError):
    """A model-backed capability was invoked with no model provider configured.

    Raised instead of falling back to a deterministic answer. A caller that asked
    for the smart path and received a lesser result without being told has been
    misled about what it received.
    """

    code = "no_provider"


class ProfileInvalidError(SmartToolError):
    """A profile failed to parse, so nothing can be launched from it."""

    code = "profile_invalid"


class ProfileNotFoundError(SmartToolError):
    """The named profile does not resolve to a file on this host."""

    code = "profile_not_found"


class EnvironmentNotFoundError(SmartToolError):
    """No environment on this host carries the given id."""

    code = "environment_not_found"


class EnvironmentLimitError(SmartToolError):
    """Launching would exceed the concurrent environment ceiling.

    Environments are long-lived and nothing reaps them, so the ceiling is what
    stands between a fan-out and an exhausted host.
    """

    code = "environment_limit"


class OperationFailedError(SmartToolError):
    """An environment operation ran and did not succeed."""

    code = "operation_failed"


class OperationTimedOutError(SmartToolError):
    """An environment operation exceeded its time budget."""

    code = "timed_out"


class GenerationFailedError(SmartToolError):
    """The model did not produce a usable answer within the attempt budget."""

    code = "generation_failed"

    def __init__(self, message: str, remedy: str, *, attempts: list[dict]) -> None:
        super().__init__(message, remedy)
        self.attempts = attempts
