"""Push and pull: copy between the host and the twin with `docker cp` semantics."""

from contextlib import suppress
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import signal
import stat
import subprocess
import tarfile
from tempfile import TemporaryFile
import time
from typing import BinaryIO

from python_on_whales import ClientNotFoundError
from python_on_whales.components.container.cli_wrapper import Container
from python_on_whales.exceptions import DockerException

from digital_twin_universe.capabilities.universe import state
from digital_twin_universe.capabilities.universe.compose import compose_client, running_twin, translate_docker_error
from digital_twin_universe.schemas import DigitalTwinUniverseError, Transfer

# `docker cp` resolves container paths against `/`, so probes in the twin do the same.
TWIN_ROOT = "/"


MAX_PUSH_FILES = 100_000
MAX_PUSH_FILE_BYTES = 128 * 1024 * 1024
MAX_PUSH_CONTENT_BYTES = 1024 * 1024 * 1024
MAX_PUSH_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_PUSH_DIAGNOSTIC_BYTES = 16 * 1024 * 1024
PUSH_TIMEOUT_SECONDS = 120


def push_files(id: str, source: Path, destination: str) -> Transfer:
    """Upload with daemon-applied ownership, without requiring capabilities inside the twin."""
    with state.lifecycle_lock(id):
        twin = running_twin(state.read(id))
        original_source = str(source)
        source = Path(source).expanduser().absolute()
        if not source.exists() and not source.is_symlink():
            raise DigitalTwinUniverseError(
                "source-not-found",
                f"{source} does not exist on the host.",
                "Pass a file or directory that exists.",
            )
        if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
            raise _push_error(
                "transfer-unsupported",
                "Safe uploads need no-follow directory reads available on Linux, WSL, and macOS.",
            )
        if not destination or "\0" in destination:
            raise _push_error("transfer-path-invalid", "Choose a nonempty container destination without NUL bytes.")
        destination = posixpath.normpath("/" + destination.lstrip("/"))
        # Match docker cp's destination-symlink behavior before constructing the outer upload archive.
        if _in_twin(twin, f"test -L {_quote(destination)} && echo yes") == "yes":
            destination = _in_twin(twin, f"readlink -f {_quote(destination)}")
            if not destination.startswith("/"):
                raise _push_error("transfer-path-invalid", "The destination symlink could not be resolved safely.")
        landing = destination
        if _in_twin(twin, f"test -d {_quote(destination)} && echo yes") == "yes":
            landing = str(PurePosixPath(destination) / source.name)
        owner = _in_twin(twin, 'printf \'%s:%s\' "$(id -u)" "$(id -g)"')
        if not re.fullmatch(r"[0-9]+:[0-9]+", owner):
            raise _push_error("transfer-owner-unknown", "The twin's numeric user and group could not be read.")
        uid, gid = (int(value) for value in owner.split(":"))
        target = PurePosixPath(landing)
        if target.name in ("", ".", ".."):
            raise _push_error("transfer-path-invalid", "Choose a named destination file or directory.")
        budget = _PushBudget(deadline=time.monotonic() + PUSH_TIMEOUT_SECONDS)
        with TemporaryFile() as spool:
            try:
                with tarfile.open(fileobj=spool, mode="w", format=tarfile.PAX_FORMAT) as archive:
                    parent = os.open(source.parents[0].resolve(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        _upload_entry(parent, source.name, target.name, archive, uid, gid, budget)
                    finally:
                        os.close(parent)
            except OSError as error:
                raise _push_error("transfer-source-changed", "The selected source could not be read safely.") from error
            spool.seek(0)
            command = [*compose_client(id).docker_cmd, "cp", "-", f"{twin.id}:{target.parents[0]}"]
            _upload_archive(command, spool, budget.deadline)
        return Transfer(source=original_source, destination=landing, files=budget.files)


@dataclass
class _PushBudget:
    deadline: float
    entries: int = 0
    files: int = 0
    content_bytes: int = 0

    def check(self) -> None:
        if time.monotonic() > self.deadline:
            raise _push_error("transfer-timeout", "The bounded upload deadline expired.")
        if self.entries > MAX_PUSH_FILES or self.content_bytes > MAX_PUSH_CONTENT_BYTES:
            raise _push_error("transfer-limit", "The upload exceeds its selected-entry or content-byte limit.")


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_size, metadata.st_mtime_ns


def _upload_entry(
    parent: int,
    name: str,
    archived: str,
    archive: tarfile.TarFile,
    uid: int,
    gid: int,
    budget: _PushBudget,
) -> None:
    budget.entries += 1
    budget.check()
    if len(PurePosixPath(archived).parts) > 128:
        raise _push_error("transfer-limit", "The upload exceeds 128 directory levels.")
    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    info = tarfile.TarInfo(archived)
    info.uid, info.gid = uid, gid
    info.mode, info.mtime = stat.S_IMODE(before.st_mode), before.st_mtime
    if stat.S_ISLNK(before.st_mode):
        info.type, info.linkname = tarfile.SYMTYPE, os.readlink(name, dir_fd=parent)
    elif stat.S_ISDIR(before.st_mode):
        info.type = tarfile.DIRTYPE
    elif stat.S_ISREG(before.st_mode):
        info.size = before.st_size
        if info.size > MAX_PUSH_FILE_BYTES:
            raise _push_error("transfer-limit", f"Selected file {archived!r} exceeds the per-file upload limit.")
        budget.content_bytes += info.size
    else:
        raise _push_error(
            "transfer-file-unsupported", f"Selected path {archived!r} is not a file, directory, or symlink."
        )
    budget.check()
    serialized = len(info.tobuf(format=tarfile.PAX_FORMAT)) + ((info.size + 511) // 512) * 512
    if archive.offset + serialized + tarfile.RECORDSIZE > MAX_PUSH_ARCHIVE_BYTES:
        raise _push_error("transfer-limit", "The upload exceeds its archive-byte limit.")
    if info.isreg():
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as source:
            if _identity(os.fstat(source.fileno())) != _identity(before):
                raise _push_error("transfer-source-changed", f"Selected file {archived!r} changed before reading.")
            archive.addfile(info, source)
            if _identity(os.fstat(source.fileno())) != _identity(before):
                raise _push_error("transfer-source-changed", f"Selected file {archived!r} changed while reading.")
        budget.files += 1
    elif info.isdir():
        descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            if _identity(os.fstat(descriptor)) != _identity(before):
                raise _push_error("transfer-source-changed", f"Selected directory {archived!r} changed before reading.")
            archive.addfile(info)
            names = []
            with os.scandir(descriptor) as children:
                for child in children:
                    if len(names) + budget.entries >= MAX_PUSH_FILES:
                        raise _push_error("transfer-limit", "The upload exceeds its selected-entry limit.")
                    names.append(child.name)
                    budget.check()
            for child in sorted(names):
                _upload_entry(descriptor, child, f"{archived}/{child}", archive, uid, gid, budget)
            if _identity(os.fstat(descriptor)) != _identity(before):
                raise _push_error("transfer-source-changed", f"Selected directory {archived!r} changed while reading.")
        finally:
            os.close(descriptor)
    else:
        archive.addfile(info)
        budget.files += 1
    if _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _identity(before):
        raise _push_error("transfer-source-changed", f"Selected path {archived!r} changed during upload preparation.")
    budget.check()


def _upload_archive(command: list[str], spool: BinaryIO, deadline: float) -> None:
    if time.monotonic() > deadline:
        raise _push_error("transfer-timeout", "The bounded upload deadline expired before Docker was started.")
    with TemporaryFile() as output, TemporaryFile() as errors:
        try:
            with subprocess.Popen(
                command, stdin=spool, stdout=output, stderr=errors, start_new_session=True
            ) as process:
                try:
                    while process.poll() is None:
                        if time.monotonic() > deadline:
                            raise _push_error("transfer-timeout", "The bounded Docker upload deadline expired.")
                        if (
                            max(os.fstat(output.fileno()).st_size, os.fstat(errors.fileno()).st_size)
                            > MAX_PUSH_DIAGNOSTIC_BYTES
                        ):
                            raise _push_error("transfer-limit", "Docker upload diagnostics exceeded their byte limit.")
                        time.sleep(0.01)
                    if (
                        max(os.fstat(output.fileno()).st_size, os.fstat(errors.fileno()).st_size)
                        > MAX_PUSH_DIAGNOSTIC_BYTES
                    ):
                        raise _push_error("transfer-limit", "Docker upload diagnostics exceeded their byte limit.")
                    if process.returncode:
                        raise _push_error("transfer-failed", "Docker could not upload the selected archive.")
                finally:
                    # Stop only this upload's helpers, including ones retaining the private diagnostic files.
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    if process.poll() is None:
                        process.kill()
                        process.wait()
        except FileNotFoundError as error:
            raise _push_error("docker-unavailable", "The Docker executable could not be found.") from error


def _push_error(code: str, message: str) -> DigitalTwinUniverseError:
    return DigitalTwinUniverseError(
        code,
        message,
        "Use a smaller stable source on a supported host and a writable existing container parent. "
        "Host files and permissions are unchanged; inspect any partially copied container path before retrying.",
    )


def pull_files(id: str, source: str, destination: Path) -> Transfer:
    """Copy a file or directory out of the twin onto the host."""
    twin = running_twin(state.read(id))
    destination = Path(destination)
    if _in_twin(twin, f"test -e {_quote(source)} && echo yes") != "yes":
        raise DigitalTwinUniverseError(
            "source-not-found",
            f"{source} does not exist in the twin of universe {id}.",
            f"Pass a path that exists in the twin; `digital-twin-universe exec --id {id} --command 'ls -la {source}'` shows it.",
        )
    landing = destination / PurePosixPath(source).name if destination.is_dir() else destination
    _copy(id, (twin.name, source), destination)
    return Transfer(source=source, destination=str(landing), files=_count_files(landing))


def _copy(id: str, source: Path | tuple[str, str], destination: Path | tuple[str, str]) -> None:
    try:
        compose_client(id).container.copy(source, destination)
    except (ClientNotFoundError, DockerException) as error:
        translated = translate_docker_error(error)
        if translated is not None:
            raise translated from error
        raise DigitalTwinUniverseError(
            "transfer-failed",
            f"`docker cp` refused the transfer for universe {id}: {(getattr(error, 'stderr', None) or '').strip()}",
            "Check both paths; a destination's parent directory must exist, and a file cannot land on a directory.",
        ) from error


def _in_twin(twin: Container, script: str) -> str:
    """Run a shell snippet in the twin as its own user and return its stdout; a non-zero exit is an empty string."""
    output = twin.execute(["sh", "-c", f"{script} 2>/dev/null || true"], workdir=TWIN_ROOT)
    return str(output).strip() if output is not None else ""


def _quote(path: str) -> str:
    return "'" + path.replace("'", "'\\''") + "'"


def _count_files(path: Path) -> int:
    if path.is_dir():
        return sum(1 for entry in path.rglob("*") if entry.is_file())
    return 1 if path.exists() else 0
