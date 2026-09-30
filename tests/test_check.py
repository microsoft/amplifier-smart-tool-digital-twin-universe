import json
import shutil

import pytest
from typer.testing import CliRunner

from digital_twin_universe.capabilities.check import check as check_module
from digital_twin_universe.cli import app
from digital_twin_universe.lib import check
from digital_twin_universe.schemas import Prerequisite

runner = CliRunner()


def _present(name: str, detail: str) -> Prerequisite:
    return Prerequisite(name=name, present=True, detail=detail)


def _absent(name: str) -> Prerequisite:
    return Prerequisite(name=name, present=False, detail=f"{name} missing", remedy=f"install {name}")


def test_report_carries_versions_when_everything_is_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check_module, "_probe_cli", lambda: _present("docker-cli", "/usr/bin/docker"))
    monkeypatch.setattr(check_module, "_probe_daemon", lambda: _present("docker-daemon", "29.0.0"))
    monkeypatch.setattr(check_module, "_probe_compose", lambda: _present("docker-compose", "v5.0.0"))

    report = check()

    assert report.ok
    assert report.docker_version == "29.0.0"
    assert report.compose_version == "v5.0.0"
    assert [prerequisite.name for prerequisite in report.prerequisites] == [
        "docker-cli",
        "docker-daemon",
        "docker-compose",
    ]


def test_probing_stops_at_the_first_missing_prerequisite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check_module, "_probe_cli", lambda: _present("docker-cli", "/usr/bin/docker"))
    monkeypatch.setattr(check_module, "_probe_daemon", lambda: _absent("docker-daemon"))
    monkeypatch.setattr(check_module, "_probe_compose", lambda: pytest.fail("compose was probed without a daemon"))

    report = check()

    assert not report.ok
    assert report.docker_version is None
    assert report.compose_version is None
    assert report.prerequisites[-1].remedy == "install docker-daemon"


def test_cli_missing_is_reported_without_touching_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check_module.shutil, "which", lambda _: None)

    report = check()

    assert not report.ok
    assert [prerequisite.name for prerequisite in report.prerequisites] == ["docker-cli"]
    assert report.prerequisites[0].remedy is not None
    assert check_module.DOCKER_INSTALL_URL in report.prerequisites[0].remedy


def test_cli_prints_the_report_and_exits_by_its_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check_module, "_probe_cli", lambda: _absent("docker-cli"))

    result = runner.invoke(app, ["check"])

    assert result.exit_code == 1
    assert json.loads(result.stdout)["ok"] is False


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs the Docker CLI")
def test_live_docker_is_probed_end_to_end() -> None:
    report = check()

    assert report.prerequisites[0].present
    if report.ok:
        assert report.docker_version
        assert report.compose_version
