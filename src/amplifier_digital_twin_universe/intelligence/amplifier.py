"""The shipped implementation of the model seam, on the amplifier-agent engine.

Every engine import is deferred into a function body for two reasons: importing
`amplifier_agent_lib` rewrites `os.environ["AMPLIFIER_HOME"]`, and a deterministic
capability must never pay for a provider stack it does not use.

A turn here is a single self-contained model call. Tools and sub-agents are
unmounted before boot, so a prompt cannot cause the engine to read, write, or run
anything on the host. This tool proposes; the caller disposes.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
import os
import sys
from typing import Any
import uuid

from amplifier_digital_twin_universe.errors import MissingPrerequisiteError, NoProviderError, OperationFailedError
from amplifier_digital_twin_universe.intelligence.schemas import ModelRequest, ModelResult

WORKSPACE = "amplifier-digital-twin-universe"
MODEL_ENV_VAR = "AMPLIFIER_DTU_MODEL"
PROVIDER_ENV_VAR = "AMPLIFIER_DTU_PROVIDER"

# A provider is usable only when its client library is importable. Credentials
# alone resolve a provider the engine then fails to mount, which reads to a
# caller as a model that answered badly rather than one that never ran.
PROVIDER_PACKAGES = {
    "anthropic": ("anthropic",),
    "openai": ("openai",),
    "azure-openai": ("openai",),
    "gemini": ("google.genai", "google.generativeai"),
}


class AmplifierIntelligence:
    """Runs prompts through the amplifier-agent engine."""

    implementation = "amplifier-agent"

    def available_providers(self) -> list[str]:
        """Providers this environment can actually run: credentials and client both.

        Deterministic. Reads credentials but calls no model.
        """
        from amplifier_agent_cli.provider_sources import enumerate_resolvable_providers

        return [name for name in enumerate_resolvable_providers() if missing_package(name) is None]

    def preflight(self, provider: str | None = None) -> str:
        preferred = provider or os.environ.get(PROVIDER_ENV_VAR) or None
        resolvable = self.available_providers()
        if preferred:
            absent = missing_package(preferred)
            if absent is not None:
                raise NoProviderError(
                    f"Model provider {preferred!r} has credentials but its client library is not installed.",
                    f"Install it, for example `uv tool install --with {absent} amplifier-digital-twin-universe`.",
                )
            if preferred not in resolvable:
                raise NoProviderError(
                    f"Model provider {preferred!r} has no resolvable credentials.",
                    f"Set the credential environment variable for {preferred!r}, "
                    f"or choose one of: {', '.join(resolvable) or 'none available'}.",
                )
            return preferred
        if not resolvable:
            raise NoProviderError(
                "This capability is model-backed and no model provider is configured.",
                "Set ANTHROPIC_API_KEY (or OPENAI_API_KEY, GOOGLE_API_KEY, AZURE_OPENAI_API_KEY) and run again.",
            )
        return resolvable[0]

    def run(self, request: ModelRequest) -> ModelResult:
        """Run one turn. Call from synchronous code; each call owns its event loop."""
        return asyncio.run(self.run_async(request))

    async def run_async(self, request: ModelRequest) -> ModelResult:
        _require_git()
        provider = self.preflight(request.provider)
        model = request.model or os.environ.get(MODEL_ENV_VAR) or None

        from amplifier_agent_cli.provider_sources import inject_provider, inject_routing_matrix
        from amplifier_agent_lib import __version__
        from amplifier_agent_lib._runtime import make_turn_handler
        from amplifier_agent_lib.bundle.cache import load_and_prepare_cached
        from amplifier_agent_lib.engine import Engine
        from amplifier_agent_lib.protocol import PROTOCOL_VERSION, server_default_capabilities
        from amplifier_agent_lib.protocol_points.defaults_cli import (
            ApprovalOverride,
            CliApprovalSystem,
            CliDisplaySystem,
        )

        prepared = await load_and_prepare_cached(aaa_version=__version__)

        # The vendored bundle declares provider stubs, and injection is a no-op
        # while any provider is mounted. Without this clear the injection is
        # discarded.
        prepared.mount_plan["providers"] = []
        # A turn is text in, text out. Unmounting the tools and sub-agents makes
        # that a property of the engine rather than a promise in a prompt. The
        # hooks go too: they observe a session this tool does not have, and each
        # one is a third-party module whose failure to load would fail the turn.
        prepared.mount_plan["tools"] = []
        prepared.mount_plan["agents"] = {}
        prepared.mount_plan["hooks"] = []
        inject_provider(prepared, provider, model_override=model)
        inject_routing_matrix(prepared, provider)

        handler = make_turn_handler(prepared, cwd=None, is_resumed=False, workspace=WORKSPACE)
        engine = Engine(
            turn_handler=handler,
            protocol_points={
                # Nothing is mounted that could ask for approval. Declining anything
                # that somehow does keeps the unmounting from being the only defense.
                "approval": CliApprovalSystem(override=ApprovalOverride.NO),
                "display": CliDisplaySystem(stream=sys.stderr, verbosity="quiet"),
            },
        )
        await engine.boot(
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": dict(server_default_capabilities()),
                "sessionId": "",
                "resume": False,
            },
            bundle_override=prepared,
        )
        try:
            result: dict[str, Any] = await engine.submit_turn(
                {"sessionId": "", "turnId": f"turn-{uuid.uuid4().hex}", "prompt": request.prompt}
            )
        finally:
            await engine.shutdown()

        tokens_in = int(result.get("tokensIn") or 0)
        tokens_out = int(result.get("tokensOut") or 0)
        reply = result.get("reply") or ""
        if tokens_in == 0 and tokens_out == 0:
            # The engine reports a mount failure as a reply rather than raising.
            # Passing that on as an answer would present a tool that never ran as
            # a model that answered badly.
            raise OperationFailedError(
                f"The agent engine returned without reaching a model: {reply or 'no reply'}",
                "Check that the chosen provider's credentials and client library are both present, "
                "then run `check` to see which providers this host resolves.",
            )

        cost = result.get("costUsd")
        return ModelResult(
            text=reply,
            provider=provider,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost if isinstance(cost, Decimal) or cost is None else Decimal(str(cost)),
        )


def missing_package(provider: str) -> str | None:
    """The client library a provider needs and this environment lacks, if any."""
    from importlib.util import find_spec

    candidates = PROVIDER_PACKAGES.get(provider)
    if not candidates:
        return None
    for candidate in candidates:
        try:
            if find_spec(candidate) is not None:
                return None
        except (ImportError, ValueError):
            continue
    return candidates[0].split(".")[0]


def _require_git() -> None:
    """The engine fetches its modules by cloning, so git must be on PATH."""
    from shutil import which

    if which("git") is None:
        raise MissingPrerequisiteError(
            "git was not found on PATH.",
            "Install git. The agent engine fetches its modules by cloning repositories.",
        )
