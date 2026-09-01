"""The environment surface: what it refuses, and how it names a failure."""

import json
import subprocess

import pytest

from amplifier_digital_twin_universe import environments
from amplifier_digital_twin_universe.cli import main
from amplifier_digital_twin_universe.errors import (
    EnvironmentLimitError,
    EnvironmentNotFoundError,
    MissingPrerequisiteError,
    OperationFailedError,
    OperationTimedOutError,
    ProfileInvalidError,
    ProfileNotFoundError,
)


def test_a_profile_is_named_by_a_path_that_exists(tmp_path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text("base:\n  image: ubuntu:24.04\n", encoding="utf-8")
    assert environments.resolve_profile(profile) == profile.resolve()


def test_a_bare_profile_name_fails_saying_paths_are_what_resolve() -> None:
    """Names that live inside the engine's repository do not resolve from a package."""
    with pytest.raises(ProfileNotFoundError) as raised:
        environments.resolve_profile("amplifier-chat")
    assert "path" in raised.value.remedy.lower()


def test_the_environment_ceiling_is_read_from_the_environment(monkeypatch) -> None:
    monkeypatch.delenv(environments.MAX_ENVIRONMENTS_ENV_VAR, raising=False)
    assert environments.max_environments() == environments.DEFAULT_MAX_ENVIRONMENTS
    assert environments.max_environments(3) == 3

    monkeypatch.setenv(environments.MAX_ENVIRONMENTS_ENV_VAR, "2")
    assert environments.max_environments() == 2

    monkeypatch.setenv(environments.MAX_ENVIRONMENTS_ENV_VAR, "not-a-number")
    with pytest.raises(ValueError, match=environments.MAX_ENVIRONMENTS_ENV_VAR):
        environments.max_environments()


def test_launching_past_the_ceiling_is_refused_before_anything_is_created(tmp_path, monkeypatch) -> None:
    """Nothing reaps environments, so the ceiling is the only thing that bounds a fan-out."""
    profile = tmp_path / "profile.yaml"
    profile.write_text("base:\n  image: ubuntu:24.04\n", encoding="utf-8")
    monkeypatch.setattr(environments, "list_environments", lambda: [{"id": "one"}, {"id": "two"}])

    with pytest.raises(EnvironmentLimitError) as raised:
        environments.launch(profile, max_environments=2)
    assert environments.MAX_ENVIRONMENTS_ENV_VAR in raised.value.remedy


def test_an_empty_command_is_rejected_by_the_library_not_only_the_cli() -> None:
    with pytest.raises(ValueError, match="command"):
        environments.run("dtu-1a2b3c4d", [])


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (RuntimeError("Environment not found: dtu-1a2b3c4d"), EnvironmentNotFoundError),
        (RuntimeError("provisioning command failed"), OperationFailedError),
        (subprocess.TimeoutExpired("incus", 10), OperationTimedOutError),
        (ValueError("Profile must specify base.image"), ProfileInvalidError),
        (FileNotFoundError("no such profile"), ProfileNotFoundError),
    ],
)
def test_engine_failures_arrive_carrying_a_code_and_a_remedy(raised, expected) -> None:
    """A caller branches on a code. A bare RuntimeError gives it nothing to branch on."""

    def explode() -> None:
        raise raised

    with pytest.raises(expected) as caught:
        environments._call("doing the thing", explode)
    assert caught.value.code
    assert caught.value.remedy


def test_an_unreachable_runtime_reads_as_a_missing_prerequisite() -> None:
    from amplifier_bundle_digital_twin_universe.incus import IncusError

    def explode() -> None:
        raise IncusError("incus daemon is unreachable")

    with pytest.raises(MissingPrerequisiteError):
        environments._call("listing environments", explode)


def test_cli_reports_a_missing_prerequisite_with_its_own_exit_code(capsys, monkeypatch) -> None:
    def explode() -> None:
        raise MissingPrerequisiteError("incus is not installed.", "Install incus.")

    monkeypatch.setattr("amplifier_digital_twin_universe.cli.list_environments", explode)
    assert main(["list"]) == 4
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "missing_prerequisite"
    assert error["remedy"]


def test_cli_exec_splits_one_command_line_into_arguments(capsys, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(environment_id, command, *, timeout):
        seen["command"] = command
        return {"id": environment_id, "command": " ".join(command), "exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr("amplifier_digital_twin_universe.cli.run", fake_run)
    assert main(["exec", "--id", "dtu-1", "--command", "echo 'hello world'"]) == 0
    assert seen["command"] == ["echo", "hello world"]
    assert json.loads(capsys.readouterr().out)["result"]["exit_code"] == 0


def test_cli_exec_succeeds_when_the_command_itself_fails(capsys, monkeypatch) -> None:
    """Running a command that exits non-zero is a result, not a failure of the call."""
    monkeypatch.setattr(
        "amplifier_digital_twin_universe.cli.run",
        lambda *_args, **_kwargs: {"id": "dtu-1", "command": "false", "exit_code": 1, "stdout": "", "stderr": ""},
    )
    assert main(["exec", "--id", "dtu-1", "--command", "false"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["exit_code"] == 1


@pytest.mark.parametrize(
    ("ready", "exit_code"),
    [(True, 0), (None, 0), (False, 5)],
)
def test_cli_readiness_exit_code_separates_not_ready_from_no_checks(capsys, monkeypatch, ready, exit_code) -> None:
    """A profile with no checks is not a profile that failed its checks."""
    monkeypatch.setattr(
        "amplifier_digital_twin_universe.cli.check_readiness",
        lambda *_args, **_kwargs: {"ready": ready, "message": "measured"},
    )
    assert main(["check-readiness", "--id", "dtu-1"]) == exit_code
    assert json.loads(capsys.readouterr().out)["result"]["ready"] == ready
