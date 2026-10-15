"""Private uploads need daemon-applied ownership, not capabilities inside the twin."""

from collections.abc import Iterator
from contextlib import nullcontext
import os
from pathlib import Path
import stat
import sys
import tarfile
from tempfile import TemporaryFile
import time
from types import SimpleNamespace
from typing import Any, BinaryIO

import pytest

from digital_twin_universe.capabilities.universe import files
from digital_twin_universe.schemas import DigitalTwinUniverseError

pytestmark = pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="Secure uploads require POSIX no-follow reads")


@pytest.fixture
def upload(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    observed: dict[str, Any] = {"owner": "0:0", "directory": False, "symlink": False, "uploads": []}
    twin = SimpleNamespace(id="immutable-container-id", name="reusable-name")
    monkeypatch.setattr(files.state, "lifecycle_lock", lambda id: nullcontext())
    monkeypatch.setattr(files.state, "read", lambda id: object())
    monkeypatch.setattr(files, "running_twin", lambda record: twin)
    monkeypatch.setattr(
        files, "compose_client", lambda id: SimpleNamespace(docker_cmd=["docker", "--host", "unix:///owned.sock"])
    )

    def probe(container: Any, script: str) -> str:
        assert container is twin
        if script.startswith("test -L"):
            return "yes" if observed["symlink"] else ""
        if script.startswith("readlink"):
            return "/resolved/directory"
        if script.startswith("test -d"):
            return "yes" if observed["directory"] else ""
        assert script.startswith("printf")
        return observed["owner"]

    def capture(command: list[str], spool: BinaryIO, deadline: float) -> None:
        assert stat.S_IMODE(os.fstat(spool.fileno()).st_mode) == 0o600
        assert deadline > time.monotonic()
        with tarfile.open(fileobj=spool) as archive:
            members = archive.getmembers()
            contents = {}
            for member in members:
                if member.isfile():
                    stream = archive.extractfile(member)
                    assert stream is not None
                    contents[member.name] = stream.read()
        observed["uploads"].append((command, members, contents))

    monkeypatch.setattr(files, "_in_twin", probe)
    monkeypatch.setattr(files, "_upload_archive", capture)
    return observed


@pytest.mark.parametrize("owner", ["0:0", "1000:1001"])
def test_private_file_uses_numeric_container_owner_without_host_chmod_or_chown(
    tmp_path: Path, upload: dict[str, Any], owner: str
) -> None:
    source = tmp_path / "private.tar"
    source.write_bytes(b"private source bytes")
    source.chmod(0o600)
    before = source.stat()
    upload["owner"] = owner
    transfer = files.push_files("owned", source, "/artifacts/source.tar")
    command, members, contents = upload["uploads"][0]
    assert command == ["docker", "--host", "unix:///owned.sock", "cp", "-", "immutable-container-id:/artifacts"]
    assert [(member.name, member.uid, member.gid, member.mode) for member in members] == [
        ("source.tar", *map(int, owner.split(":")), 0o600)
    ]
    assert contents == {"source.tar": b"private source bytes"}
    assert source.read_bytes() == b"private source bytes"
    after = source.stat()
    assert (after.st_mode, after.st_uid, after.st_gid, after.st_mtime_ns) == (
        before.st_mode,
        before.st_uid,
        before.st_gid,
        before.st_mtime_ns,
    )
    assert transfer.source == str(source)
    assert transfer.destination == "/artifacts/source.tar"
    assert transfer.files == 1


def test_directory_modes_and_symlink_text_are_preserved_without_reading_targets(
    tmp_path: Path, upload: dict[str, Any]
) -> None:
    secret = tmp_path / "outside"
    secret.mkdir()
    (secret / "secret").write_text("must never be copied")
    source = tmp_path / "selected"
    source.mkdir()
    (source / "nested").mkdir()
    (source / "nested" / "run").write_bytes(b"execute")
    (source / "nested" / "run").chmod(0o751)
    (source / "external").symlink_to(secret, target_is_directory=True)
    (source / "dangling").symlink_to("../missing")
    upload.update(owner="1000:1001", directory=True)
    transfer = files.push_files("owned", source, "workspace")
    command, members, contents = upload["uploads"][0]
    assert command[-1] == "immutable-container-id:/workspace"
    assert transfer.destination == "/workspace/selected"
    assert transfer.files == 3
    assert contents == {"selected/nested/run": b"execute"}
    by_name = {member.name: member for member in members}
    assert by_name["selected/nested/run"].mode == 0o751
    assert by_name["selected/external"].issym()
    assert by_name["selected/external"].linkname == str(secret)
    assert by_name["selected/dangling"].linkname == "../missing"
    assert all((member.uid, member.gid) == (1000, 1001) for member in members)


def test_root_symlink_and_destination_symlink_keep_copy_semantics(tmp_path: Path, upload: dict[str, Any]) -> None:
    source = tmp_path / "link"
    source.symlink_to("missing")
    upload.update(directory=True, symlink=True)
    transfer = files.push_files("owned", source, "/alias")
    command, members, contents = upload["uploads"][0]
    assert command[-1] == "immutable-container-id:/resolved/directory"
    assert transfer.destination == "/resolved/directory/link"
    assert members[0].issym()
    assert members[0].linkname == "missing"
    assert contents == {}


def test_changed_source_is_not_uploaded_or_rewritten(
    tmp_path: Path, upload: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"before")
    original = tarfile.TarFile.addfile

    def mutate(archive: tarfile.TarFile, info: tarfile.TarInfo, fileobj: BinaryIO | None = None) -> None:
        original(archive, info, fileobj)
        source.write_bytes(b"peer changed this source")

    monkeypatch.setattr(tarfile.TarFile, "addfile", mutate)
    with pytest.raises(DigitalTwinUniverseError) as raised:
        files.push_files("owned", source, "/workspace/input")
    assert raised.value.code == "transfer-source-changed"
    assert source.read_bytes() == b"peer changed this source"
    assert upload["uploads"] == []


@pytest.mark.parametrize(
    ("limit", "value"),
    [("MAX_PUSH_FILES", 1), ("MAX_PUSH_FILE_BYTES", 3), ("MAX_PUSH_CONTENT_BYTES", 3), ("MAX_PUSH_ARCHIVE_BYTES", 512)],
)
def test_limits_reject_before_docker_upload(
    tmp_path: Path, upload: dict[str, Any], monkeypatch: pytest.MonkeyPatch, limit: str, value: int
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_bytes(b"1234")
    monkeypatch.setattr(files, limit, value)
    with pytest.raises(DigitalTwinUniverseError) as raised:
        files.push_files("owned", source, "/workspace/input")
    assert raised.value.code == "transfer-limit"
    assert upload["uploads"] == []


def test_unknown_owner_fails_before_upload(tmp_path: Path, upload: dict[str, Any]) -> None:
    source = tmp_path / "source"
    source.touch()
    upload["owner"] = ""
    with pytest.raises(DigitalTwinUniverseError) as raised:
        files.push_files("owned", source, "/workspace/input")
    assert raised.value.code == "transfer-owner-unknown"
    assert upload["uploads"] == []


def test_source_replaced_by_fifo_cannot_block_preparation(
    tmp_path: Path, upload: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"before")
    original_stat = os.stat

    def replace_after_stat(path: Any, **kwargs: Any) -> os.stat_result:
        observed = original_stat(path, **kwargs)
        if path == source.name and kwargs.get("dir_fd") is not None:
            source.unlink()
            os.mkfifo(source)
        return observed

    monkeypatch.setattr(os, "stat", replace_after_stat)
    started = time.monotonic()
    with pytest.raises(DigitalTwinUniverseError) as raised:
        files.push_files("owned", source, "/workspace/input")
    assert raised.value.code == "transfer-source-changed"
    assert time.monotonic() - started < 1
    assert upload["uploads"] == []
    assert stat.S_ISFIFO(original_stat(source).st_mode)


def test_no_unsafe_platform_fallback(tmp_path: Path, upload: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    source.touch()
    monkeypatch.delattr(os, "O_NOFOLLOW")
    with pytest.raises(DigitalTwinUniverseError) as raised:
        files.push_files("owned", source, "/workspace/input")
    assert raised.value.code == "transfer-unsupported"
    assert "WSL" in raised.value.message
    assert upload["uploads"] == []


@pytest.fixture
def spool() -> Iterator[BinaryIO]:
    with TemporaryFile() as stream:
        stream.write(b"archive bytes")
        stream.seek(0)
        yield stream


def test_upload_subprocess_reads_archive(spool: BinaryIO) -> None:
    files._upload_archive(
        [sys.executable, "-c", "import sys; assert sys.stdin.buffer.read() == b'archive bytes'"],
        spool,
        time.monotonic() + 5,
    )


@pytest.mark.parametrize(
    ("script", "duration", "code"),
    [("raise SystemExit(2)", 5, "transfer-failed"), ("import time; time.sleep(10)", 0.1, "transfer-timeout")],
)
def test_upload_subprocess_failure_is_bounded(spool: BinaryIO, script: str, duration: float, code: str) -> None:
    started = time.monotonic()
    with pytest.raises(DigitalTwinUniverseError) as raised:
        files._upload_archive([sys.executable, "-c", script], spool, started + duration)
    assert raised.value.code == code
    assert time.monotonic() - started < 3


def test_upload_diagnostics_are_bounded(spool: BinaryIO, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(files, "MAX_PUSH_DIAGNOSTIC_BYTES", 100)
    with pytest.raises(DigitalTwinUniverseError) as raised:
        files._upload_archive([sys.executable, "-c", "print('x' * 1000)"], spool, time.monotonic() + 5)
    assert raised.value.code == "transfer-limit"


def test_owned_upload_helpers_cannot_outlive_main_process(spool: BinaryIO, tmp_path: Path) -> None:
    late = tmp_path / "late"
    child = f"import time; from pathlib import Path; time.sleep(0.3); Path({str(late)!r}).touch()"
    script = f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {child!r}])"
    files._upload_archive([sys.executable, "-c", script], spool, time.monotonic() + 5)
    time.sleep(0.4)
    assert not late.exists()
