from pathlib import Path

import pytest
from python_on_whales.exceptions import DockerException

from digital_twin_universe.capabilities.universe import profile as profile_module
from digital_twin_universe.capabilities.universe.profile import (
    EXAMPLES_DIRECTORY,
    PROFILE_DIRECTORY,
    load,
    resolve_path,
)
from digital_twin_universe.schemas import DigitalTwinUniverseError

ONE_SERVICE = "name: demo\nservices:\n  app:\n    image: alpine\n"


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git project with a `demo` profile, with the working directory in a subdirectory of it."""
    (tmp_path / ".git").mkdir()
    profile_dir = tmp_path / PROFILE_DIRECTORY / "demo"
    profile_dir.mkdir(parents=True)
    (profile_dir / "compose.yaml").write_text(ONE_SERVICE)
    nested = tmp_path / "src" / "deep"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    return profile_dir / "compose.yaml"


def test_a_compose_file_path_resolves_to_itself(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text(ONE_SERVICE)

    assert resolve_path(compose) == compose.resolve()
    assert resolve_path(str(compose)) == compose.resolve()


def test_a_directory_resolves_to_the_compose_file_inside_it(tmp_path: Path) -> None:
    (tmp_path / "compose.yaml").write_text(ONE_SERVICE)

    assert resolve_path(tmp_path) == (tmp_path / "compose.yaml").resolve()


def test_a_directory_without_a_compose_file_is_not_a_profile(tmp_path: Path) -> None:
    with pytest.raises(DigitalTwinUniverseError) as raised:
        resolve_path(tmp_path)

    assert raised.value.code == "profile-not-found"
    assert "compose.yaml" in raised.value.message


def test_a_name_is_found_under_the_profile_directory_from_a_subdirectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = _project(tmp_path, monkeypatch)

    assert resolve_path("demo") == expected.resolve()


def test_a_name_falls_back_to_the_shipped_examples(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, monkeypatch)

    assert resolve_path("copilot-cli") == (EXAMPLES_DIRECTORY / "copilot-cli" / "compose.yaml").resolve()


def test_an_unknown_name_says_where_it_looked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, monkeypatch)

    with pytest.raises(DigitalTwinUniverseError) as raised:
        resolve_path("nope")

    assert raised.value.code == "profile-not-found"
    assert str(tmp_path / PROFILE_DIRECTORY / "nope") in raised.value.message
    assert str(EXAMPLES_DIRECTORY / "nope") in raised.value.message


def test_a_missing_path_is_not_treated_as_a_name(tmp_path: Path) -> None:
    with pytest.raises(DigitalTwinUniverseError) as raised:
        resolve_path(tmp_path / "missing" / "compose.yaml")

    assert raised.value.code == "profile-not-found"
    assert "Looked for" not in raised.value.message


def test_the_only_service_is_the_twin_when_x_dtu_is_absent(tmp_path: Path) -> None:
    profile = profile_module._profile_from_config(tmp_path, {"name": "demo", "services": {"app": {}}})

    assert profile.twin_machine == "app"
    assert profile.description is None


def test_a_service_named_twin_is_the_twin_among_several(tmp_path: Path) -> None:
    config = {"name": "demo", "services": {"db": {}, "twin": {}}}

    assert profile_module._profile_from_config(tmp_path, config).twin_machine == "twin"


def test_x_dtu_names_the_twin_and_description(tmp_path: Path) -> None:
    config = {
        "name": "demo",
        "services": {"db": {}, "app": {}},
        "x-dtu": {"twin_machine": "app", "description": "An app"},
    }
    profile = profile_module._profile_from_config(tmp_path, config)

    assert profile.twin_machine == "app"
    assert profile.description == "An app"
    assert profile.services == ["db", "app"]


def test_a_twin_that_is_not_a_service_is_invalid(tmp_path: Path) -> None:
    config = {"name": "demo", "services": {"db": {}, "app": {}}, "x-dtu": {"twin_machine": "web"}}

    with pytest.raises(DigitalTwinUniverseError) as raised:
        profile_module._profile_from_config(tmp_path, config)

    assert raised.value.code == "profile-invalid"
    assert "'web'" in raised.value.message


def test_an_unset_variable_is_named_with_the_profile_message(tmp_path: Path) -> None:
    stderr = b"error while interpolating services.app.environment.TOKEN: required variable TOKEN is missing a value: Set TOKEN first"
    error = DockerException(["docker", "compose", "config"], 1, stderr=stderr)

    translated = profile_module._profile_error(tmp_path, error)

    assert translated.code == "env-missing"
    assert "TOKEN" in translated.message
    assert "Set TOKEN first" in translated.message


def test_any_other_compose_rejection_is_profile_invalid(tmp_path: Path) -> None:
    error = DockerException(["docker", "compose", "config"], 1, stderr=b"services.app must be a mapping")

    translated = profile_module._profile_error(tmp_path, error)

    assert translated.code == "profile-invalid"
    assert "services.app must be a mapping" in translated.message


def test_load_reads_the_profile_back_through_compose(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    compose = tmp_path / "compose.yaml"
    compose.write_text(ONE_SERVICE)

    class FakeCompose:
        def config(self, return_json: bool) -> dict:
            return {"name": "demo", "services": {"app": {"image": "alpine"}}}

    class FakeClient:
        compose = FakeCompose()

    monkeypatch.setattr(profile_module, "compose_client", lambda compose_files, profiles: FakeClient())

    profile = load(compose)

    assert profile.path == compose.resolve()
    assert profile.name == "demo"
    assert profile.twin_machine == "app"
