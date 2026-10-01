"""Picks the agent provider and the model with the installed SDKs faked, so it runs on a bare install."""

from importlib.machinery import ModuleSpec

import click
import pytest
from typer.testing import CliRunner

from digital_twin_universe import lib
from digital_twin_universe.cli import app
from digital_twin_universe.intelligence import interface
from digital_twin_universe.intelligence.interface import resolve_agent_provider, select_intelligence
from digital_twin_universe.intelligence.schemas import AgentRequest, AgentResult
from digital_twin_universe.schemas import DEFAULT_INTELLIGENCE_MODELS, AgentProvider, DigitalTwinUniverseError

REPOSITORY = "git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe"

runner = CliRunner()


class FakeIntelligence:
    def __init__(self) -> None:
        self.implementation = "fake"

    def preflight(self) -> None:
        pass

    def run(self, request: AgentRequest) -> AgentResult:
        return AgentResult()


def only_installed(monkeypatch: pytest.MonkeyPatch, *agent_providers: AgentProvider) -> None:
    modules = {interface.SDK_MODULES[agent_provider] for agent_provider in agent_providers}

    def find_spec(name: str) -> ModuleSpec | None:
        return ModuleSpec(name, None) if name in modules else None

    monkeypatch.setattr(interface, "find_spec", find_spec)


@pytest.mark.parametrize(
    ("installed", "named", "picked"),
    [
        (("copilot", "amplifier-agent"), None, "copilot"),
        (("copilot",), None, "copilot"),
        (("amplifier-agent",), None, "amplifier-agent"),
        (("copilot", "amplifier-agent"), "amplifier-agent", "amplifier-agent"),
    ],
)
def test_a_named_agent_provider_wins_and_otherwise_the_first_installed_is_picked(
    monkeypatch: pytest.MonkeyPatch,
    installed: tuple[AgentProvider, ...],
    named: AgentProvider | None,
    picked: AgentProvider,
) -> None:
    only_installed(monkeypatch, *installed)

    assert resolve_agent_provider(named) == picked


@pytest.mark.parametrize(
    ("named", "code", "extras"),
    [
        (None, "no-agent-provider", ["all", "copilot", "amplifier-agent"]),
        ("copilot", "agent-provider-not-installed", ["copilot"]),
        ("amplifier-agent", "agent-provider-not-installed", ["amplifier-agent"]),
    ],
)
def test_a_missing_agent_provider_names_the_install_command(
    monkeypatch: pytest.MonkeyPatch, named: AgentProvider | None, code: str, extras: list[str]
) -> None:
    only_installed(monkeypatch)

    with pytest.raises(DigitalTwinUniverseError) as failure:
        resolve_agent_provider(named)

    assert failure.value.code == code
    assert f'uv tool install "digital-twin-universe[{extras[0]}] @ {REPOSITORY}"' in failure.value.remedy
    for extra in extras:
        assert f"[{extra}]" in failure.value.remedy


def test_an_injected_intelligence_wins_and_needs_no_agent_provider_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    only_installed(monkeypatch)
    fake = FakeIntelligence()

    assert select_intelligence(fake, None, None, DEFAULT_INTELLIGENCE_MODELS) == (
        fake,
        DEFAULT_INTELLIGENCE_MODELS["copilot"],
    )
    assert select_intelligence(fake, "amplifier-agent", None, DEFAULT_INTELLIGENCE_MODELS) == (
        fake,
        DEFAULT_INTELLIGENCE_MODELS["amplifier-agent"],
    )
    assert select_intelligence(fake, None, "anthropic/claude-opus-5", DEFAULT_INTELLIGENCE_MODELS) == (
        fake,
        "anthropic/claude-opus-5",
    )


@pytest.mark.parametrize("agent_provider", ["copilot", "amplifier-agent"])
def test_without_an_injected_intelligence_the_agent_providers_default_model_is_asked_for(
    monkeypatch: pytest.MonkeyPatch, agent_provider: AgentProvider
) -> None:
    only_installed(monkeypatch, "copilot", "amplifier-agent")
    fake = FakeIntelligence()
    resolved: list[tuple[AgentProvider | None, str | None]] = []

    def resolve(named: AgentProvider | None, model: str | None) -> FakeIntelligence:
        resolved.append((named, model))
        return fake

    monkeypatch.setattr(interface, "resolve_intelligence", resolve)

    selected = select_intelligence(None, agent_provider, None, DEFAULT_INTELLIGENCE_MODELS)

    assert selected == (fake, DEFAULT_INTELLIGENCE_MODELS[agent_provider])
    assert resolved == [(agent_provider, DEFAULT_INTELLIGENCE_MODELS[agent_provider])]


def test_the_install_cli_passes_the_agent_provider_and_model_through(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[tuple[object, ...]] = []

    def install(*arguments: object) -> object:
        received.append(arguments)
        raise DigitalTwinUniverseError("stop", "Stopped after the arguments were read.", "Nothing to do.")

    monkeypatch.setattr(lib, "install", install)

    result = runner.invoke(
        app, ["install", "--agent-provider", "amplifier-agent", "--model", "anthropic/claude-opus-5"]
    )

    assert result.exit_code == 1
    assert received[0][2:4] == ("amplifier-agent", "anthropic/claude-opus-5")


def test_the_create_profile_cli_passes_the_agent_provider_and_model_through(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[tuple[object, ...]] = []

    def create_profile(*arguments: object) -> object:
        received.append(arguments)
        raise DigitalTwinUniverseError("stop", "Stopped after the arguments were read.", "Nothing to do.")

    monkeypatch.setattr(lib, "create_profile", create_profile)

    result = runner.invoke(
        app, ["create-profile", "--description", "an app", "--agent-provider", "copilot", "--model", "gpt-6-sol"]
    )

    assert result.exit_code == 1
    assert received[0][7:9] == ("copilot", "gpt-6-sol")


@pytest.mark.parametrize("command", ["install", "create-profile"])
def test_the_help_names_the_agent_providers_and_their_default_models(command: str) -> None:
    result = runner.invoke(app, [command, "-h"], env={"COLUMNS": "400"})
    # Typer forces color under GITHUB_ACTIONS, and Rich styles each dash of an option on its own.
    output = click.unstyle(result.output)

    assert result.exit_code == 0
    assert "--agent-provider" in output
    for agent_provider, model in DEFAULT_INTELLIGENCE_MODELS.items():
        assert f"{agent_provider}: {model}" in output
