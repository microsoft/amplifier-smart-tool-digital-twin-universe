# Profile Reference

A profile is a [Compose file](https://docs.docker.com/reference/compose-file/) with one extra top-level block, `x-dtu`.
Compose ignores `x-*` keys, so the same file runs under plain `docker compose up`; `x-dtu` is what Digital Twin Universe adds on top.
`validate-profile` checks it, and `launch` runs it together with an overlay the tool renders beside it.

Everything Compose can express, the profile expresses the Compose way; this page covers only what the tool reads from it and what it adds.

## Where profiles live

A profile usually belongs to the project it tests, under `.agents/digital-twin-universe/<profile-name>/`. `compose.yaml` is the entry point; the twin's `Dockerfile` and anything it copies in sit beside it.

```
.agents/digital-twin-universe/
  codex-cli/
    compose.yaml
    Dockerfile
    config.toml
  web-app-postgres/
    compose.yaml
    Dockerfile
```

`launch --profile codex-cli` resolves to that directory, searching from the working directory upward to the git root, so it works from any subdirectory of the project. `create-profile` writes here. Nothing else is special about the location: it is where an agent looks first, and where the next agent finds what the last one made.

`create-profile` works in `<name>.draft/` beside the final directory, at the same depth, so `repositories[].path` and `build: .` resolve identically before and after the rename to `<name>/` that a passing verification earns. A failed run leaves the draft in place and says so; a person can fix it by hand, and `launch --profile .agents/digital-twin-universe/<name>.draft` runs it by path.

A name not found in the project is looked for under the examples shipped inside the package, `examples/<name>/` beside the tool's own files. `launch --profile copilot-cli` works on a fresh install for that reason, and `launch --profile hello` is the smallest universe there is.

An environment defined anywhere else launches by path. `launch --profile <path>` takes:

- A Compose file.
- A directory holding `compose.yaml` or `docker-compose.yaml`.

A Compose file without `x-dtu` is a valid profile when it has one service. Add `x-dtu` when it has several, or to serve repositories, rewrite hosts, or restrict egress.

## Example

GitHub Copilot CLI, installed the way its README says to and signed in with the host's `gh` token. This is the shipped `examples/copilot-cli`, so it launches by name:

```yaml
# Base of the project name; launch appends a short id.
name: copilot-cli

x-dtu:
  description: GitHub Copilot CLI installed as a user would install it
  twin_machine: copilot

services:
  copilot:
    # The Dockerfile beside this file, built with the universe's network up.
    build: .
    # Read from the host at launch, never written to any file. Unset, it fails with this message.
    environment:
      GH_TOKEN: ${GH_TOKEN:?run `export GH_TOKEN=$(gh auth token)` on the host}
    # Keeps the twin up for exec.
    command: sleep infinity
    # launch waits for this before reporting ready.
    healthcheck:
      test: [CMD, copilot, --version]
```

```dockerfile
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates git
RUN useradd -m user
# exec runs as this user, in WORKDIR.
USER user
WORKDIR /home/user
RUN curl -fsSL https://gh.io/copilot-install | bash
ENV PATH="/home/user/.local/bin:${PATH}"
```

Then:

```bash
export GH_TOKEN=$(gh auth token)
digital-twin-universe launch --profile copilot-cli
digital-twin-universe exec --id <id> --command 'copilot -p "Reply with exactly the word: dtu-ok" --silent'
digital-twin-universe exec --id <id>              # interactive: `copilot` opens its TUI, already signed in
```

`${GH_TOKEN:?message}` is Compose's own syntax; `validate-profile` and `launch` both honor it.

Three things make a service a good twin. It runs as a created user, not `root`, the way a person's machine does. Its `command` keeps running, because a base image with no long-running process exits and leaves nothing to `exec` into; anything that needs a secret or has to be up goes in `command`, since `build` does not see runtime environment. And it has a `healthcheck`, so `launch` knows when it is ready.

## `x-dtu`

### `description`

Optional. One or two sentences for `list` and the dashboard.

### `twin_machine`

A profile has one service the software under test is installed and run in, and any number of services it talks to. The first is the twin machine: the stand-in for the user's machine, and the implicit target of `exec`, `file-push`, `file-pull`, and `status`.

`twin_machine` names that service. It can be omitted when the file has one service, or when a service is named `twin`.

### `urls`

Optional. Paths and labels for the URLs `launch`, `status`, and `list` report, by the container port the twin listens on. The host side is whatever `ports` published it to, read from Docker at launch, so `${PORT:-8410}:8000` and a bare `8000` both work. Without an entry, a published port is reported as `http://localhost:<host port>/`; with several entries on one port, each becomes a URL, in the order written.

```yaml
urls:
  - port: 8000
    path: /chat/
    label: Chat UI
    host: chat.localhost
```

`path` starts with `/`. A `port` the twin does not publish over TCP is the error `url-port-unpublished`.

`host` is the name the URL is reported with, `localhost` by default. It changes nothing about the universe: the port is still published on the host's loopback, and the name has to get the client there on its own. The tool cannot make a name resolve without editing the hosts file, which it never does, so a name is a promise about the client, and the safe one is under `.localhost`, which RFC 6761 reserves for loopback:

```
Chrome, Edge, Firefox          every OS         resolve *.localhost to 127.0.0.1 themselves
curl 7.78 and later            every OS         same
Linux with systemd-resolved    system-wide      Python, Node, wget, everything
Safari                         macOS            does not; needs a hosts entry
Python, Node, wget, ping       macOS, Windows   use the system resolver, which does not; needs a hosts entry
WSL                            Windows          no systemd-resolved by default; same as macOS for non-browsers
```

So `http://chat.localhost:8410/` opens in a browser on any machine, and a script on a Mac hitting the same URL fails with an unknown host until `127.0.0.1 chat.localhost` is in `/etc/hosts`. Give a script the `localhost` URL, or an entry with no `host`, when that matters.

Any other name (`chat.test`, `app.example.com`) is looked up from the machine running `validate-profile`: when it does not resolve to a loopback address there, the warning `url-host-unresolved` names the hosts entry that would fix it. `*.localhost` is exempt from the lookup, since the system resolver is exactly the client that may not honor it. Public wildcard DNS such as `127.0.0.1.nip.io` passes the lookup and works in every client, at the cost of depending on the internet and a third party.

The twin is not told its name. Requests arrive with the `Host` header the client sent, so an app that routes or sets cookies by hostname behaves as it would with a real one, but an app that builds absolute URLs learns its hostname from its own configuration, which the profile sets in `environment` as a deployment would.

### `repositories`

Local git repositories served from inside the universe, so the twin can clone and install them from the URL they will have once released. Each has:

- `path`: a repository on the host, relative to the profile. Every local branch and tag is served. The checked-out branch is the default and carries the working tree, so uncommitted changes are tested too, and a consumer pinning `@main` still finds `main` while the developer sits on a fix branch. A shallow clone serves only the checked-out branch, as one commit holding the working tree.
- `url`: optional. The URL the repository stands in for, such as `https://github.com/microsoft/amplifier-core`. A trailing `.git` or `/` is ignored. Without `url`, the repository is reachable only at the git server's own address, `http://git:3000/dtu/<repo>`.

A request matches a `url` when the host is the same and the path, with a `.git` at the repository boundary ignored, is the `url` path or continues from it with `/`. Query and fragment take no part, and the comparison ignores case, as GitHub and Gitea both do. So `https://github.com/microsoft/amplifier` matches every way a tool reaches that repository over its host:

```
/microsoft/amplifier                                        pip and uv git+https, browsers
/microsoft/amplifier/                                       trailing slash
/microsoft/amplifier.git/info/refs?service=git-upload-pack  git clone, fetch
/microsoft/amplifier/info/refs?service=git-upload-pack      git clone without .git
/microsoft/amplifier.git/git-upload-pack                    git fetch
/microsoft/amplifier.git/git-receive-pack                   git push
/microsoft/amplifier.git/HEAD                               dumb http
/microsoft/amplifier/archive/refs/heads/main.tar.gz         pip install <archive url>
/microsoft/amplifier/releases/download/v1.0.0/x.whl         release assets
/microsoft/amplifier/raw/main/README.md                     raw files via github.com
/microsoft/amplifier?go-get=1                               go modules
/Microsoft/Amplifier.git/info/refs                          case differences
```

and no other repository, however close its name:

```
/microsoft/amplifier-foundation
/microsoft/amplifier_old
/microsoft/amplifierx
/microsoft/amplifier.js
/microsoft/amplifier.wiki.git
/microsoft/amplifier.github.io
/other/amplifier
```

The matched prefix is replaced by the served repository's path and the rest is kept, so `git clone`, `pip install git+https://...`, `uv tool install git+https://...`, and archive downloads all land on the served copy. When several `url`s share a host, the longest path wins.

The unit of rewriting is one repository: serve `amplifier-foundation` and every request for it, on any path, goes to the local copy, while `amplifier`, `amplifier-core`, and every `amplifier-bundle-*` still go to GitHub. To rewrite several, list each one.

Only that host is rewritten. Tools that reach a repository some other way are not: SSH clones are not HTTP; `gh` and uv's GitHub fast path use `api.github.com`; npm's `github:` specifier and GitHub tarballs use `codeload.github.com`; `raw.githubusercontent.com` is its own host. `launch` sets `UV_NO_GITHUB_FAST_PATH=true` so uv falls back to `git fetch`, which is rewritten. The rest go to the real host, and a served repository is not there.

The server is Gitea, so the archive endpoints an installer downloads, the API a tool queries, and the web pages a person opens all answer as well as `git clone` does. Nothing in the profile depends on that: it is one service in the overlay, reached only through the URL the profile already wrote.

### `rewrites`

Optional. `host/path` prefixes routed to another URL, for anything that is not a repository. The target is usually a service in the same file, which is how a mock stands in for a real host:

```yaml
rewrites:
  - match: api.openai.com
    target: http://openai-mock:8080
```

The twin then calls `https://api.openai.com` with no configuration change and reaches the mock, TLS included. Same matching as `repositories`.

### `allow`

Optional. Hostnames the universe may reach. When present, everything else is refused at the gateway, and the refusal is in the gateway's logs. Served and rewritten hosts are always allowed.

## What `launch` adds

The overlay, rendered into the universe's state directory and run as a second `-f` file, never edits the profile:

- A `git` service when `repositories` is set: Gitea, at `http://git:3000/dtu/<name>`. Each repository is copied from its read-only mount, its working tree committed in the copy, and pushed in. The host checkout is never written to, and its own hooks never run.
- A `gateway` service when any `repositories[].url`, `rewrites`, or `allow` is set. It is the only route out. It terminates TLS for the hosts it rewrites, with a certificate authority it mints on first start, and tunnels every other host through untouched, with that host's real certificate. An `allow` list is the exception: refusing a request means reading it, so every host is terminated when one is set.
- On every service when the gateway is present: the authority mounted at `/etc/dtu/ca.crt` and named in `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `GIT_SSL_CAINFO`, `NODE_EXTRA_CA_CERTS`, `PIP_CERT`, and `CURL_CA_BUNDLE`, with `UV_NATIVE_TLS=true` so uv uses it and `NODE_USE_ENV_PROXY=1` so Node uses the proxy at all; the gateway in `HTTP_PROXY` and `HTTPS_PROXY` with `NO_PROXY=localhost,127.0.0.1,::1,git,gateway`; `UV_NO_GITHUB_FAST_PATH=true` when a `github.com` URL is rewritten; and `depends_on` on what the universe provides.
- On every service that builds: the proxy as `build.args`, which BuildKit passes to `RUN` without being asked, and the authority as the `dtu-ca` build context, which a Dockerfile has to copy from. See below. A service that names no `image` is given `<id>-<service>`, the name Compose would choose, so the image `launch` builds is the one Compose runs.
- One network the whole universe shares.

The mounted authority is the public roots with the universe's own appended, because every variable that names a bundle replaces the trust store rather than adding to it. A tunnelled host and a rewritten host both verify.

The service names `git` and `gateway` are reserved. A profile with no `repositories[].url`, no `rewrites`, and no `allow` renders no gateway, no authority, and no proxy in anyone's environment.

### Trusting the universe during a build

A build reaches the gateway on its own: BuildKit predefines the proxy arguments. Nothing else reaches it. A build argument no `ARG` declares is invisible to `RUN`, so the authority cannot be handed to a build the way it is handed to a running container, and a build that clones a rewritten URL has to say that it trusts the universe:

```dockerfile
COPY --from=dtu-ca ca.crt /usr/local/share/ca-certificates/digital-twin-universe.crt
RUN update-ca-certificates
```

Two lines, and only for a profile that installs during the build rather than at runtime. Without them the universe still works everywhere else, since `launch` mounts the same file into the running container. `examples/served-repository` is a worked profile that does it.

Both lines are Debian and Alpine shaped. An image whose certificates live elsewhere writes its own equivalent; the build context is called `dtu-ca` and the file in it is `ca.crt`.

## What `validate-profile` checks

`launch` runs all of it first and refuses a profile with any error, before anything is recorded or started.

`docker compose config` first, so the Compose side is validated by Compose itself, interpolation included, and an unset `${NAME}` fails naming the variable. Then `x-dtu` against its schema, then the invariants that make the file a universe rather than just a Compose project. Errors:

- `x-dtu.twin_machine` names a service that is not in the file, or is missing when there are several services and none is named `twin`.
- A service is named `git` or `gateway`.
- A `repositories[].path` is not a git repository, or `url` has no host and path.
- A `urls[].port` the twin does not publish over TCP.
- A service sets `network_mode`, `networks`, `dns`, or `extra_hosts` while the gateway would be present, since each bypasses it.
- The twin is gated behind a Compose `profiles:` entry that would leave it out.
- Windows and Linux services in one file.

The codes are `twin-missing`, `reserved-service-name`, `repository-not-git`, `repository-url-invalid`, `url-port-unpublished`, `routes-around-gateway`, `twin-excluded-by-compose-profile`, `mixed-platforms`, and `x-dtu-invalid`, plus `env-missing` and `profile-invalid` from Compose itself.

Warnings, since each is legitimate sometimes but usually not what a realistic profile means:

- The twin has no `command`, `entrypoint`, or image `CMD` that keeps running.
- The twin bind-mounts a host path, which mutates the host and stands in for the clone and install a real user would do.
- The twin has no `healthcheck`, so `launch` cannot wait for it to be ready.
- The twin runs as `root`.
- A `urls[].host` outside `.localhost` that this machine does not resolve to loopback.

Their codes are `no-long-running-command`, `bind-mount`, `no-healthcheck`, `runs-as-root`, and `url-host-unresolved`.

## A change to someone else's repository

The universe serves a checkout; where the checkout came from is not its concern. So testing a change to an upstream repository, before it is a pull request or anything else, is one clone away:

```bash
git clone https://github.com/microsoft/amplifier-core ~/work/amplifier-core
cd ~/work/amplifier-core && git checkout -b fix-thing
# edit; commit or do not
```

```yaml
repositories:
  - path: ~/work/amplifier-core
    url: https://github.com/microsoft/amplifier-core
```

Inside the universe, `pip install git+https://github.com/microsoft/amplifier-core` and everything that fetches from that URL get the fix, and `microsoft/amplifier-foundation`, `microsoft/amplifier-bundle-*`, and every other repository still come from GitHub. Serve several to change several. The consumer never learns that anything was rewritten, which is the point: it installs what it would install on release day.

The tool's own `.agents/digital-twin-universe/self-validation/` is this pattern applied to itself, with `path: ../../..`.

## Adapting an existing Compose file

A project that already has a `compose.yaml` and a `Dockerfile` is most of the way there:

1. Copy them into `.agents/digital-twin-universe/<profile-name>/` rather than pointing at the originals, since the profile will diverge from the development setup.
2. Add `x-dtu` with `twin_machine` naming the service the code under test runs in.
3. Replace bind mounts of source code with the clone and install a user would do. Declare the repository under `x-dtu.repositories` and have the `Dockerfile` or `command` install it from its URL.
4. Pass secrets as `${NAME}` in `environment` rather than through a committed `.env`.

A development Compose file mounts source and runs a dev server; a profile installs a release and runs it. Both are ordinary Compose files, so the change is in what the file says, not in how it is written.

## Less common Compose settings the tool honors

- `privileged: true` with a Docker-in-Docker image or an installed daemon gives a nested Docker daemon. This weakens the container boundary and is the profile's decision to make; Linux twins only.
- `platform: windows/amd64` on a Windows host in Windows containers mode gives a Windows twin.
- `depends_on` with `condition: service_healthy` orders dependencies before the twin; `launch` waits on every healthcheck in the file, not only the twin's.

## What the profile does not do

- Update in place. Change the profile and `launch` again; Docker's layer cache makes an unchanged `Dockerfile` prefix free.
- Override package indexes. Serve the repository and install from git instead.
