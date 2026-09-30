"""Docker's own documentation, held locally so the agent reads it rather than recalls it.

`docker/docs` is the Hugo source of docs.docker.com: every page is a Markdown file under `content/` at the path
of its URL. The whole tree is mostly Desktop, Hub, and Scout material a profile author never needs, so the clone
is sparse and shallow: `--filter=blob:none` downloads no contents up front and the sparse checkout names what to
materialize. The Dockerfile instruction reference is not in that repository; the site mounts it from BuildKit at
build time, so it is fetched separately as one file.

Everything here is best effort. Without git or without network the reference is reported absent and the run
goes on; the prompt then tells the agent to say so rather than guess.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
import shutil
import ssl
import subprocess
from urllib.error import URLError
from urllib.request import Request, urlopen

import certifi
from pydantic import BaseModel

from digital_twin_universe.capabilities.install.facts import Runner, remaining

REFERENCE_ROOT = Path.home() / ".digital-twin-universe" / "reference"
DOCS_REPOSITORY = "https://github.com/docker/docs"
DOCS_DIRECTORY = "docker-docs"
DOCKERFILE_REFERENCE_URL = (
    "https://raw.githubusercontent.com/moby/buildkit/master/frontend/dockerfile/docs/reference.md"
)
DOCKERFILE_REFERENCE = "dockerfile-reference.md"
STAMP = "fetched-at"
REFRESH_AFTER = timedelta(days=7)
FETCH_TIMEOUT = 120
SPARSE_PATHS = (
    "content/reference/compose-file",
    "content/manuals/compose",
    "content/manuals/build/building",
    "content/manuals/build/concepts",
)


class Reference(BaseModel):
    """Where the local documentation is, and why any of it is missing."""

    docs: Path | None
    dockerfile: Path | None
    fetched_at: datetime | None
    notes: list[str]


def ensure_reference(runner: Runner, deadline: float, root: Path = REFERENCE_ROOT) -> Reference:
    """The reference as it is after one refresh attempt: cloned when absent, pulled when stale, otherwise as found."""
    docs = root / DOCS_DIRECTORY
    dockerfile = root / DOCKERFILE_REFERENCE
    stamp = root / STAMP
    notes: list[str] = []
    fetched_at = _read_stamp(stamp)
    fresh = fetched_at is not None and datetime.now(UTC) - fetched_at < REFRESH_AFTER
    if docs.is_dir() and dockerfile.is_file() and fresh:
        return Reference(docs=docs, dockerfile=dockerfile, fetched_at=fetched_at, notes=[])
    if shutil.which("git") is None:
        notes.append("git is not installed, so the Docker documentation could not be cloned.")
    else:
        notes += _clone_or_pull(runner, deadline, docs)
    notes += _fetch_dockerfile_reference(dockerfile, deadline)
    if docs.is_dir() and dockerfile.is_file() and not notes:
        root.mkdir(parents=True, exist_ok=True)
        fetched_at = datetime.now(UTC)
        stamp.write_text(fetched_at.isoformat(), encoding="utf-8")
    return Reference(
        docs=docs if docs.is_dir() else None,
        dockerfile=dockerfile if dockerfile.is_file() else None,
        fetched_at=fetched_at,
        notes=notes,
    )


def _clone_or_pull(runner: Runner, deadline: float, docs: Path) -> list[str]:
    def git(*argv: str) -> str | None:
        try:
            result = runner.run(["git", *argv], min(FETCH_TIMEOUT, remaining(deadline)))
        except subprocess.TimeoutExpired:
            return f"`git {argv[0]}` did not finish within {FETCH_TIMEOUT}s"
        return None if result.exit_code == 0 else f"`git {argv[0]}` exited {result.exit_code}: {result.stderr.strip()}"

    if docs.is_dir():
        problem = git("-C", str(docs), "pull", "--ff-only", "-q")
        return (
            [f"The Docker documentation clone could not be refreshed ({problem}); the older copy is used."]
            if problem
            else []
        )
    docs.parent.mkdir(parents=True, exist_ok=True)
    problem = git("clone", "--depth", "1", "--filter=blob:none", "--sparse", "-q", DOCS_REPOSITORY, str(docs))
    if problem is None:
        problem = git("-C", str(docs), "sparse-checkout", "set", *SPARSE_PATHS)
    if problem is None:
        return []
    shutil.rmtree(docs, ignore_errors=True)
    return [f"The Docker documentation could not be cloned from {DOCS_REPOSITORY} ({problem})."]


def _fetch_dockerfile_reference(dockerfile: Path, deadline: float) -> list[str]:
    request = Request(DOCKERFILE_REFERENCE_URL, headers={"User-Agent": "digital-twin-universe"})
    context = ssl.create_default_context(cafile=certifi.where())
    try:
        with urlopen(request, timeout=min(FETCH_TIMEOUT, remaining(deadline)), context=context) as response:
            text = response.read().decode("utf-8", errors="replace")
    except (URLError, OSError, TimeoutError) as error:
        if dockerfile.is_file():
            return [f"The Dockerfile reference could not be refreshed ({error}); the older copy is used."]
        return [f"The Dockerfile reference could not be fetched from {DOCKERFILE_REFERENCE_URL} ({error})."]
    if not text.strip():
        return [f"{DOCKERFILE_REFERENCE_URL} answered with an empty page."]
    dockerfile.parent.mkdir(parents=True, exist_ok=True)
    dockerfile.write_text(text, encoding="utf-8")
    return []


def _read_stamp(stamp: Path) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
