"""The contract every model-backed capability runs through, so the implementation is swappable."""

from importlib.util import find_spec
from typing import Protocol

from digital_twin_universe.core.skill import DISTRIBUTION, repository_url
from digital_twin_universe.intelligence.schemas import AgentRequest, AgentResult
from digital_twin_universe.schemas import AGENT_PROVIDERS, AgentProvider, DigitalTwinUniverseError

# The import each agent provider's SDK answers to; its extra is named after the agent provider.
SDK_MODULES: dict[AgentProvider, str] = {
    "copilot": "copilot",
    "amplifier-agent": "amplifier_agent",
    "codex": "openai_codex",
}


class Intelligence(Protocol):
    """Runs agents against models; the library depends on this contract, never on an SDK."""

    implementation: str

    def preflight(self) -> None:
        """Raise DigitalTwinUniverseError naming exactly what to configure when the implementation cannot run."""
        ...

    def run(self, request: AgentRequest) -> AgentResult:
        """Run one agent to completion.

        When the request carries an output schema, the result either holds a conforming
        `output` or an `error`; retry mechanics are the implementation's own business.
        """
        ...


def installed(agent_provider: AgentProvider) -> bool:
    """Whether the agent provider's SDK is importable, without importing it."""
    return find_spec(SDK_MODULES[agent_provider]) is not None


def resolve_agent_provider(agent_provider: AgentProvider | None = None) -> AgentProvider:
    """The named agent provider when it is installed, else the first installed one in `AGENT_PROVIDERS` order."""
    if agent_provider is not None:
        if not installed(agent_provider):
            raise DigitalTwinUniverseError(
                "agent-provider-not-installed",
                f"The {agent_provider} agent provider is not installed.",
                f"Install digital-twin-universe with it: `uv tool install {_install_source(agent_provider)}`, or "
                f"`uv add {_install_source(agent_provider)}` in a project that uses the library.",
            )
        return agent_provider
    for candidate in AGENT_PROVIDERS:
        if installed(candidate):
            return candidate
    raise DigitalTwinUniverseError(
        "no-agent-provider",
        "Model-backed capabilities need an agent provider and none is installed.",
        f"Install digital-twin-universe with every one: `uv tool install {_install_source('all')}`, or put "
        + " or ".join(f"[{candidate}]" for candidate in AGENT_PROVIDERS)
        + " in place of [all] for just one. Use `uv add` instead of `uv tool install` in a project that uses the "
        "library.",
    )


def resolve_intelligence(agent_provider: AgentProvider | None = None, model: str | None = None) -> Intelligence:
    """The shipped implementation for the agent provider `resolve_agent_provider` picks.

    `model` is what its preflight checks, when the implementation can check one before a run.
    """
    match resolve_agent_provider(agent_provider):
        case "copilot":
            from digital_twin_universe.intelligence.copilot import CopilotIntelligence

            return CopilotIntelligence()
        case "amplifier-agent":
            from digital_twin_universe.intelligence.amplifier_agent import AmplifierAgentIntelligence

            return AmplifierAgentIntelligence() if model is None else AmplifierAgentIntelligence(model)
        case "codex":
            from digital_twin_universe.intelligence.codex import CodexIntelligence

            return CodexIntelligence()


def select_intelligence(
    intelligence: Intelligence | None,
    agent_provider: AgentProvider | None,
    model: str | None,
    default_models: dict[AgentProvider, str],
) -> tuple[Intelligence, str]:
    """The intelligence a capability runs through and the model it asks for.

    An injected intelligence wins over `agent_provider`, which then only picks the default model; with neither
    named, that default is the first agent provider's, since nothing about an injected implementation says which
    agent provider it speaks for.
    """
    if intelligence is None:
        agent_provider = resolve_agent_provider(agent_provider)
        model = default_models[agent_provider] if model is None else model
        return resolve_intelligence(agent_provider, model), model
    return intelligence, default_models[agent_provider or AGENT_PROVIDERS[0]] if model is None else model


def _install_source(extra: str) -> str:
    url = repository_url()
    return f'"{DISTRIBUTION}[{extra}] @ git+{url}"' if url else f'"{DISTRIBUTION}[{extra}]"'
