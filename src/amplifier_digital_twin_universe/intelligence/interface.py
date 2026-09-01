"""The contract every model-backed capability runs through.

A capability depends on this protocol, never on a provider SDK. Swapping the
model layer means supplying a different object with these two methods and passing
it in; no capability changes.

`default_intelligence()` imports its implementation inside the function body, so
importing this package never pulls in a provider stack. Deterministic capabilities
stay runnable with nothing configured.
"""

from __future__ import annotations

from typing import Protocol

from amplifier_digital_twin_universe.intelligence.schemas import ModelRequest, ModelResult


class Intelligence(Protocol):
    """Turns a prompt into text, and says up front when it cannot."""

    implementation: str

    def available_providers(self) -> list[str]:
        """Providers whose credentials resolve here. Empty when none do."""
        ...

    def preflight(self, provider: str | None = None) -> str:
        """Return the provider that will serve a request, or raise naming the remedy.

        Called before any prompt is built so a caller with nothing configured gets
        a precise failure instead of an authentication error from deep inside a
        provider module.
        """
        ...

    def run(self, request: ModelRequest) -> ModelResult:
        """Run one turn to completion."""
        ...


def default_intelligence() -> Intelligence:
    """The implementation this tool ships with."""
    from amplifier_digital_twin_universe.intelligence.amplifier import AmplifierIntelligence

    return AmplifierIntelligence()


def resolve(intelligence: Intelligence | None) -> Intelligence:
    """The caller's implementation, or the shipped one."""
    return intelligence if intelligence is not None else default_intelligence()
