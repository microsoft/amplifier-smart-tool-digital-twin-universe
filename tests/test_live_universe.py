"""End to end against the local Docker daemon: launch, exec, destroy. Skipped where Docker is not usable."""

import os
from pathlib import Path
import re
import subprocess
from urllib.request import urlopen

import pytest

from digital_twin_universe import lib
from digital_twin_universe.capabilities.universe.profile import EXAMPLES_DIRECTORY
from digital_twin_universe.schemas import DigitalTwinUniverseError

pytestmark = [pytest.mark.needs_docker, pytest.mark.lifecycle]

AMPLIFIER_CORE_VERSION = "99.0.0"
PROVIDER_MARKER = "DTU_PROVIDER_ANTHROPIC_TEST_MARKER"
# The Rust build of amplifier-core alone takes about three minutes.
AMPLIFIER_LAUNCH_TIMEOUT = 1200

ALPINE_PROFILE = """\
name: live
x-dtu:
  description: A twin that does nothing
  twin_machine: box
services:
  box:
    image: alpine:3.20
    command: sleep infinity
    ports: ["{port}:80"]
    healthcheck:
      test: [CMD, "true"]
      interval: 1s
"""


def test_pushed_files_land_by_docker_cp_rules_owned_by_the_twins_user_and_pull_back(
    tmp_path: Path, state_root: Path, free_port: int
) -> None:
    profile = tmp_path / "compose.yaml"
    profile.write_text(ALPINE_PROFILE.format(port=free_port).replace("command:", 'user: "1000:1000"\n    command:'))
    source = tmp_path / "src"
    (source / "nested").mkdir(parents=True)
    (source / "a.txt").write_text("a")
    (source / "nested" / "b.txt").write_text("b")

    universe = lib.launch(profile, timeout_seconds=120)
    try:
        into_existing = lib.push_files(universe.id, source, "/tmp")
        assert (into_existing.destination, into_existing.files) == ("/tmp/src", 2)

        to_new_path = lib.push_files(universe.id, source / "a.txt", "/tmp/renamed.txt")
        assert (to_new_path.destination, to_new_path.files) == ("/tmp/renamed.txt", 1)

        owners = lib.execute(universe.id, "stat -c '%u:%g %n' /tmp/src /tmp/src/nested/b.txt /tmp/renamed.txt")
        assert owners.stdout == "1000:1000 /tmp/src\n1000:1000 /tmp/src/nested/b.txt\n1000:1000 /tmp/renamed.txt\n"

        lib.execute(universe.id, "echo c > /tmp/src/c.txt")
        pulled = lib.pull_files(universe.id, "/tmp/src", tmp_path)
        assert (pulled.destination, pulled.files) == (str(tmp_path / "src"), 3)
        assert (tmp_path / "src" / "c.txt").read_text() == "c\n"

        with pytest.raises(DigitalTwinUniverseError) as raised:
            lib.push_files(universe.id, tmp_path / "missing", "/tmp")
        assert raised.value.code == "source-not-found"
        with pytest.raises(DigitalTwinUniverseError) as raised:
            lib.pull_files(universe.id, "/tmp/missing", tmp_path)
        assert raised.value.code == "source-not-found"
        with pytest.raises(DigitalTwinUniverseError) as raised:
            lib.push_files(universe.id, source / "a.txt", "/no/such/parent/a.txt")
        assert raised.value.code == "transfer-failed"
    finally:
        lib.destroy(universe.id)


def test_a_failing_healthcheck_names_the_container_and_leaves_it_for_inspection(
    tmp_path: Path, state_root: Path, free_port: int
) -> None:
    profile = tmp_path / "compose.yaml"
    profile.write_text(
        ALPINE_PROFILE.format(port=free_port).replace('[CMD, "true"]', '[CMD, "false"]\n      retries: 1')
    )

    with pytest.raises(DigitalTwinUniverseError) as raised:
        lib.launch(profile, timeout_seconds=120)

    assert raised.value.code == "unhealthy"
    ids = lib.recorded_ids()
    assert len(ids) == 1
    assert ids[0] in raised.value.remedy
    lib.destroy(ids[0])


def test_the_shipped_web_site_example_reports_named_urls_the_host_can_open(
    state_root: Path, free_port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DTU_WEB_SITE_PORT", str(free_port))

    universe = lib.launch("web-site", timeout_seconds=300)
    try:
        assert universe.state == "running"
        assert [(url.label, url.url) for url in universe.urls] == [
            ("Home", f"http://web-site.localhost:{free_port}/"),
            ("Docs", f"http://web-site.localhost:{free_port}/docs/"),
        ]
        assert lib.status(universe.id).urls == universe.urls
        assert b"<h1>Docs</h1>" in urlopen(f"http://127.0.0.1:{free_port}/docs/", timeout=5).read()
        assert lib.execute(universe.id, "id -un").stdout == "user\n"
    finally:
        lib.destroy(universe.id)


@pytest.mark.skipif(not os.environ.get("GH_TOKEN"), reason="needs GH_TOKEN for the copilot-cli example")
def test_the_shipped_copilot_example_launches_by_name(state_root: Path) -> None:
    universe = lib.launch("copilot-cli", timeout_seconds=600)
    try:
        assert universe.profile_path == (EXAMPLES_DIRECTORY / "copilot-cli" / "compose.yaml").resolve()
        assert universe.state == "running"

        result = lib.execute(universe.id, "id -un; copilot --version")
        assert result.exit_code == 0
        assert result.stdout.startswith("user\nGitHub Copilot CLI")
    finally:
        lib.destroy(universe.id)


@pytest.mark.model
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY for the amplifier-modules-dtu example"
)
def test_the_shipped_amplifier_example_runs_the_local_core_and_provider(
    tmp_path: Path, state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    core = _shallow_clone("https://github.com/microsoft/amplifier-core", tmp_path / "amplifier-core")
    provider = _shallow_clone(
        "https://github.com/microsoft/amplifier-module-provider-anthropic",
        tmp_path / "amplifier-module-provider-anthropic",
    )
    # Above any release, so uv prefers the local wheel to PyPI.
    for manifest in ("pyproject.toml", "bindings/python/Cargo.toml", "crates/amplifier-core/Cargo.toml"):
        _replace_first(core / manifest, r'(?m)^version = "[^"]+"$', f'version = "{AMPLIFIER_CORE_VERSION}"')
    _replace_first(
        core / "python" / "amplifier_core" / "__init__.py",
        r'(?m)^__version__ = "[^"]+"$',
        f'__version__ = "{AMPLIFIER_CORE_VERSION}"',
    )
    _replace_first(
        provider / "amplifier_module_provider_anthropic" / "__init__.py",
        r"(?m)^    if not api_key:$",
        f'    logger.warning("{PROVIDER_MARKER}")\n    if not api_key:',
    )
    monkeypatch.setenv("DTU_AMPLIFIER_CORE", str(core))
    monkeypatch.setenv("DTU_PROVIDER_ANTHROPIC", str(provider))

    universe = lib.launch("amplifier-modules-dtu", timeout_seconds=AMPLIFIER_LAUNCH_TIMEOUT)
    try:
        assert universe.state == "running"

        engine = lib.execute(
            universe.id,
            "~/.local/share/uv/tools/amplifier/bin/python -c "
            "'import amplifier_core._engine as engine; print(engine.__version__)'",
        )
        assert (engine.exit_code, engine.stdout) == (0, f"{AMPLIFIER_CORE_VERSION}\n")

        run = lib.execute(universe.id, 'amplifier run "Say exactly: dtu-ready"', timeout_seconds=600)
        assert run.exit_code == 0, run.stderr
        assert "dtu-ready" in run.stdout
        assert PROVIDER_MARKER in run.stdout + run.stderr
    finally:
        lib.destroy(universe.id)


def _shallow_clone(url: str, into: Path) -> Path:
    subprocess.run(["git", "clone", "-q", "--depth", "1", url, str(into)], check=True)
    return into


def _replace_first(path: Path, pattern: str, replacement: str) -> None:
    text, count = re.subn(pattern, replacement, path.read_text(), count=1)
    assert count == 1, f"{pattern!r} not found in {path}"
    path.write_text(text)
