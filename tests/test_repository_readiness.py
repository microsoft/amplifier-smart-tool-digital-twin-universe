"""Publication is ready only after every repository and all of its refs succeed."""

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

import pytest

from digital_twin_universe.capabilities.universe import overlay
from digital_twin_universe.capabilities.universe.profile import Repository


def test_repository_health_requires_completed_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(overlay.repository_snapshot, "export", lambda source, destination: None)
    service = overlay._git_service(
        tmp_path / "assets", tmp_path / "compose.yaml", [Repository(path=tmp_path / name) for name in ("a", "b")]
    )
    assert service["healthcheck"]["test"] == [
        "CMD-SHELL",
        "test -f /work/repositories-ready && wget -q -O /dev/null http://127.0.0.1:3000/api/healthz",
    ]


@pytest.mark.parametrize("failed_push", [0, 2, 3, 4])
def test_bootstrap_clears_stale_readiness_and_waits_for_every_ref(tmp_path: Path, failed_push: int) -> None:
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("Bootstrap executes inside a Linux container; this fixture requires a local POSIX shell")
    commands = tmp_path / "bin"
    commands.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    marker = work / "repositories-ready"
    marker.touch()
    for name in ("a", "b"):
        (tmp_path / "repos" / name).mkdir(parents=True)
    log = tmp_path / "pushes.jsonl"
    git = commands / "git"
    git.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "if 'symbolic-ref' in args:\n"
        "    print('main')\n"
        "elif 'push' in args:\n"
        "    assert not Path(os.environ['MARKER']).exists(), 'premature readiness'\n"
        "    log = Path(os.environ['PUSH_LOG'])\n"
        "    with log.open('a') as output:\n"
        "        output.write(json.dumps(args) + '\\n')\n"
        "    if len(log.read_text().splitlines()) == int(os.environ['FAIL_PUSH']):\n"
        "        sys.exit(3)\n"
    )
    git.chmod(0o755)
    for name in ("environment-to-ini", "chown", "su-exec", "wget"):
        command = commands / name
        command.write_text("#!/bin/sh\nexit 0\n")
        command.chmod(0o755)
    script = (overlay.TEMPLATES / overlay.BOOTSTRAP).read_text()
    for root in ("data", "repos", "work"):
        script = re.sub(rf"/{root}(?=/|\s|$)", lambda match, root=root: shlex.quote(str(tmp_path / root)), script)
    bootstrap = tmp_path / "bootstrap.sh"
    bootstrap.write_text(script)
    result = subprocess.run(
        [shell, str(bootstrap)],
        env={
            **os.environ,
            "PATH": f"{commands}{os.pathsep}{os.environ['PATH']}",
            "DTU_PASSWORD": "fixture-only",
            "MARKER": str(marker),
            "PUSH_LOG": str(log),
            "FAIL_PUSH": str(failed_push),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == (3 if failed_push else 0), result.stderr
    pushes = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(pushes) == (failed_push or 4)
    assert pushes[0][-1] == "HEAD:refs/heads/main"
    assert pushes[1][-2:] == ["refs/heads/*:refs/heads/*", "refs/tags/*:refs/tags/*"]
    if not failed_push:
        assert "/repos/b" in pushes[2][0]
    assert marker.exists() == (failed_push == 0)
