"""Select official pages deterministically and fetch their Markdown, never search results."""

import ssl
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import certifi

from digital_twin_universe.capabilities.install.facts import HostFacts, remaining
from digital_twin_universe.schemas import DigitalTwinUniverseError

DOCKER = "https://docs.docker.com"
WINDOWS = f"{DOCKER}/desktop/setup/install/windows-install/"
MACOS = f"{DOCKER}/desktop/setup/install/mac-install/"
FAQ = f"{DOCKER}/desktop/troubleshoot-and-support/faqs/general/"
WSL = f"{DOCKER}/desktop/features/wsl/"
ENGINE = f"{DOCKER}/engine/install/"
POSTINSTALL = f"{ENGINE}linux-postinstall/"
COMPOSE = f"{DOCKER}/compose/install/linux/"
MICROSOFT_WSL = "https://learn.microsoft.com/en-us/windows/wsl/install"
DISTROS = ("ubuntu", "debian", "fedora", "rhel", "centos")
ALLOWED_SOURCE_HOSTS = frozenset(("docs.docker.com", "learn.microsoft.com"))


def allowed_source(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.hostname in ALLOWED_SOURCE_HOSTS and parsed.netloc == parsed.hostname


def page_urls(facts: HostFacts) -> list[str]:
    if facts.platform == "windows":
        return [WINDOWS, WSL, FAQ, MICROSOFT_WSL]
    if facts.platform == "macos":
        return [MACOS, FAQ]
    identifiers = {facts.os_release.get("ID", ""), *facts.os_release.get("ID_LIKE", "").split()}
    if identifiers.intersection(DISTROS[:2]):
        distros = DISTROS[:2]
    elif identifiers.intersection(DISTROS[2:]):
        distros = DISTROS[2:]
    else:
        distros = DISTROS
    pages = [ENGINE, POSTINSTALL, COMPOSE, *(f"{ENGINE}{distro}/" for distro in distros)]
    return [*pages, WSL] if facts.wsl else pages


def markdown_url(source: str) -> str:
    return (
        source.rstrip("/") + ".md"
        if urlsplit(source).hostname == "docs.docker.com"
        else (source + "?accept=text/markdown")
    )


def fetch_docs(facts: HostFacts, deadline: float) -> dict[str, str]:
    urls = page_urls(facts)
    pages: dict[str, str] = {}
    context = ssl.create_default_context(cafile=certifi.where())
    for source in urls:
        request = Request(markdown_url(source), headers={"Accept": "text/markdown"})
        try:
            with urlopen(request, timeout=min(30, remaining(deadline)), context=context) as response:
                if not allowed_source(response.url):
                    raise ValueError(f"Unexpected redirect to {response.url}")
                text = response.read().decode("utf-8")
                if (
                    not text.strip()
                    or "text/html" in response.headers.get("Content-Type", "").lower()
                    or text.lstrip().lower().startswith(("<!doctype html", "<html"))
                ):
                    raise ValueError("Expected a nonempty Markdown page, not HTML")
                pages[source] = text
        except (URLError, OSError, ValueError) as error:
            remaining(deadline)
            raise DigitalTwinUniverseError(
                "docs-unreachable",
                f"Could not fetch official Docker documentation at {source}: {error}.",
                "Check network access and rerun, or read these pages by hand: " + ", ".join(urls),
            ) from error
    return pages
