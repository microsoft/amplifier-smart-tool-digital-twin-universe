"""The manifest is a contract, so it gets tested like one."""

import json
import shutil
import subprocess
import sys

import pytest

from amplifier_digital_twin_universe import MODEL_BACKED_CAPABILITIES, MissingPrerequisiteError
from amplifier_digital_twin_universe.catalog import CAPABILITIES
from amplifier_digital_twin_universe.cli import main
from amplifier_digital_twin_universe.intelligence.amplifier import _require_git
from amplifier_digital_twin_universe.manifest import load_manifest, package_version


def test_manifest_loads_through_the_library() -> None:
    manifest = load_manifest()
    assert manifest.smart_tool_format == 1
    assert manifest.name == "amplifier-digital-twin-universe"
    assert manifest.description
    assert manifest.use_cases
    assert manifest.platforms


def test_manifest_version_matches_the_package_definition() -> None:
    assert load_manifest().version == package_version()


def test_prerequisites_are_declared_with_a_doc_reference() -> None:
    for entry in load_manifest().requires:
        assert entry.install.endswith(".md") or entry.install.startswith(("http://", "https://"))


def test_optional_prerequisites_state_what_is_lost() -> None:
    optional = [r for r in load_manifest().requires if r.optional]
    assert optional
    for entry in optional:
        assert "without it" in entry.purpose.lower()


def test_every_hard_prerequisite_is_one_the_tool_detects(monkeypatch) -> None:
    """A hard requirement the tool never looks for is a failure that never fires.

    The manifest and the failure have to agree, so the set of non-optional
    entries is exactly the set the tool checks before it needs them.
    """
    assert {r.name for r in load_manifest().requires if not r.optional} == {"git"}

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(MissingPrerequisiteError) as raised:
        _require_git()
    assert raised.value.remedy


def test_cli_manifest_capability_emits_structured_output(capsys) -> None:
    assert main(["manifest"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["result"]["name"] == "amplifier-digital-twin-universe"


def test_bad_invocation_emits_an_error_envelope_naming_a_remedy(capsys) -> None:
    assert main([]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "no_capability"
    assert error["remedy"]


_CAPABILITIES = [capability.name for capability in CAPABILITIES]
_CLI = [sys.executable, "-m", "amplifier_digital_twin_universe.cli"]


@pytest.mark.parametrize("capability", _CAPABILITIES)
@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_every_capability_answers_both_help_flags(capability: str, flag: str) -> None:
    """Help a caller can only reach by already knowing the call is help it cannot use."""
    completed = subprocess.run([*_CLI, capability, flag], capture_output=True, text=True, check=False)
    assert completed.returncode == 0
    assert completed.stdout
    assert not completed.stderr


@pytest.mark.parametrize("capability", _CAPABILITIES)
def test_capability_full_help_carries_what_a_caller_needs_to_invoke_it(capability: str) -> None:
    completed = subprocess.run([*_CLI, capability, "--help"], capture_output=True, text=True, check=False)
    assert not completed.stderr
    for section in ("usage:", "Arguments:", "Returns:", "Exit codes:"):
        assert section in completed.stdout


def test_every_capability_names_a_library_function_that_exists() -> None:
    """A capability the library cannot serve is a capability the CLI invented."""
    import amplifier_digital_twin_universe as library

    for capability in CAPABILITIES:
        assert hasattr(library, capability.library), capability.name


def test_model_backed_capabilities_disclose_themselves(capsys) -> None:
    """A caller deciding whether to spend tokens can tell before spending them."""
    assert MODEL_BACKED_CAPABILITIES
    for capability in CAPABILITIES:
        completed = subprocess.run([*_CLI, capability.name, "--help"], capture_output=True, text=True, check=False)
        expected = "model-backed" if capability.model_backed else "deterministic"
        assert f"[{expected}]" in completed.stdout, capability.name


def test_terse_and_full_help_are_not_the_same_output() -> None:
    terse = subprocess.run([*_CLI, "-h"], capture_output=True, text=True, check=False)
    full = subprocess.run([*_CLI, "--help"], capture_output=True, text=True, check=False)
    assert terse.returncode == full.returncode == 0
    assert terse.stdout != full.stdout
    for capability in CAPABILITIES:
        assert capability.name in terse.stdout
        assert capability.name in full.stdout
