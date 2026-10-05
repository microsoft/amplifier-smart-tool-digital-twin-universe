import click
import pytest
import typer

# Typer 0.27 vendors Click, so building a context for its command group needs the vendored type.
from typer._click import Context
from typer.testing import CliRunner

from digital_twin_universe.cli import app
from digital_twin_universe.core import skill as skill_module
from digital_twin_universe.core.skill import CAPABILITIES
from digital_twin_universe.lib import load_manifest, skill, skill_directory, skill_resources, version
from digital_twin_universe.schemas import (
    DEFAULT_INTELLIGENCE_MODELS,
    DEFAULT_INTELLIGENCE_REASONING_EFFORT,
    Capability,
    DigitalTwinUniverseError,
)

RELATIVE_PATHS_LINE = "Relative paths in this skill are relative to the skill directory."

runner = CliRunner()


def test_manifest_body_is_the_markdown_below_the_frontmatter() -> None:
    body = load_manifest().body

    assert body
    assert not body.startswith("#")


def test_skill_is_a_wrapped_document_naming_every_capability() -> None:
    document = skill()

    assert document.startswith('<skill_content name="digital-twin-universe">')
    assert document.endswith("</skill_content>")
    assert "# digital-twin-universe" in document
    assert load_manifest().body in document
    for capability in CAPABILITIES:
        assert f"`{capability.name}` [" in document
        assert f"`digital-twin-universe {capability.name} --help`" in document


def test_repository_line_is_omitted_when_the_package_declares_no_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(skill_module, "repository_url", lambda: None)
    header = skill_module.skill().splitlines()

    assert "Repository:" not in skill_module.skill()
    assert header[2] == RELATIVE_PATHS_LINE


def test_repository_line_sits_between_the_header_lines_when_declared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(skill_module, "repository_url", lambda: "https://example.invalid/digital-twin-universe")
    header = skill_module.skill().splitlines()

    assert header[1].startswith("Skill directory: ")
    assert header[2] == "Repository: https://example.invalid/digital-twin-universe"
    assert header[3] == RELATIVE_PATHS_LINE


def test_skill_resources_resolve_under_the_skill_directory() -> None:
    root = skill_directory()
    resources = [line.removeprefix("<file>").removesuffix("</file>") for line in _resource_lines(skill())]

    assert resources
    assert resources == skill_resources()
    assert (root / "SMART_TOOL.md").is_file()
    for resource in resources:
        assert (root / resource).is_file()


def _resource_lines(document: str) -> list[str]:
    lines = [line.strip() for line in document.splitlines()]
    start = lines.index("<skill_resources>")
    end = lines.index("</skill_resources>")
    return lines[start + 1 : end]


def test_help_prints_the_skill() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert result.stdout.strip() == skill()


def test_short_help_and_no_arguments_print_the_terse_summary() -> None:
    short = runner.invoke(app, ["-h"])
    bare = runner.invoke(app, [])

    assert short.exit_code == 0
    assert "<skill_content" not in short.stdout
    assert "manifest" in short.stdout
    assert "<skill_content" not in bare.stdout


@pytest.mark.parametrize("capability", CAPABILITIES, ids=lambda capability: capability.name)
def test_a_capability_skill_has_the_tool_s_shape_and_its_own_body(capability: Capability) -> None:
    document = skill(capability.name)
    kind = "Model-backed." if capability.model_backed else "Deterministic."

    assert document.startswith(f'<skill_content name="digital-twin-universe {capability.name}">')
    assert document.endswith("</skill_content>")
    assert "Part of `digital-twin-universe`; `digital-twin-universe --help` is the tool's skill." in document
    assert f"# digital-twin-universe {capability.name}\n\n{kind}\n\n" in document
    for section in ("## Arguments", "## Result", "## Failures"):
        assert section in document


@pytest.mark.parametrize("capability", CAPABILITIES, ids=lambda capability: capability.name)
def test_a_capability_skill_names_every_cli_option(capability: Capability) -> None:
    document = skill(capability.name)
    group = typer.main.get_group(app)
    command = group.get_command(Context(group), capability.name)

    assert command is not None
    for param in command.params:
        for option in param.opts:
            if option not in ("-h", "--help"):
                assert f"`{option}" in document, option


@pytest.mark.parametrize("capability", CAPABILITIES, ids=lambda capability: capability.name)
def test_capability_help_prints_its_skill_and_short_help_the_summary(capability: Capability) -> None:
    skill_help = runner.invoke(app, [capability.name, "--help"])
    short = runner.invoke(app, [capability.name, "-h"])

    assert skill_help.exit_code == 0
    assert skill_help.stdout.strip() == skill(capability.name)
    assert short.exit_code == 0
    assert "<skill_content" not in short.stdout
    # Typer forces color under GITHUB_ACTIONS.
    assert f"Usage: root {capability.name} [OPTIONS]" in click.unstyle(short.stdout)


@pytest.mark.parametrize(
    "capability", [capability for capability in CAPABILITIES if capability.model_backed], ids=lambda c: c.name
)
def test_a_model_backed_skill_states_the_defaults_the_code_applies(capability: Capability) -> None:
    document = " ".join(skill(capability.name).split())

    for agent_provider, model in DEFAULT_INTELLIGENCE_MODELS.items():
        assert f"`{model}` on `{agent_provider}`" in document
    assert f"Defaults to `{DEFAULT_INTELLIGENCE_REASONING_EFFORT}`" in document


def test_an_unknown_capability_has_no_skill() -> None:
    with pytest.raises(DigitalTwinUniverseError) as raised:
        skill("no-such-capability")

    assert raised.value.code == "capability-unknown"


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_prints_the_distribution_and_its_installed_version(flag: str) -> None:
    result = runner.invoke(app, [flag])

    assert result.exit_code == 0
    assert result.output == f"digital-twin-universe {version()}\n"
