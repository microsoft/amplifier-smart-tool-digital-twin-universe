# Digital Twin Universe profile authoring

A Digital Twin Universe (DTU) profile is a YAML document that declares an ephemeral Incus container: its base image, the files and commands that provision it, the ports it exposes, the URLs and package indexes it resolves through, and the checks that say it is up. Launch reads the profile, creates the container, pushes files, runs setup commands in order, and fails on the first non-zero exit.

"Ready to launch" means the profile loads without a `ValueError`, emits no warnings, resolves every `${VAR}` it needs at launch time, and its `setup_cmds` install and start the target software end to end with no manual steps afterward.

## Schema

Top-level keys. `base.image` is the only hard-required field in the entire schema; every other key and every other section is optional. Unknown keys anywhere are dropped with a warning, never an error, so a typo silently loses configuration.

```yaml
name: <str>          # defaults to the YAML filename stem
description: <str>   # free-form
```

```yaml
base:                # REQUIRED
  image: <str>       # REQUIRED. e.g. ubuntu:24.04
  config:            # optional. arbitrary keys -> incus launch --config k=v
    limits.cpu: "4"
    limits.memory: "8GiB"
    security.nesting: "true"   # applied by default to every launch; set "false" to opt out
```

```yaml
url_rewrites:                        # optional; presence starts a mitmproxy HTTPS proxy
  auth:                              # optional; attaches Basic auth to every matched request
    username: <str>
    token_var: <str>                 # name of env var holding the token
  allow_uv_github_fast_path: <bool>  # default false -> exports UV_NO_GITHUB_FAST_PATH=true
  default_match_mode: boundary|prefix # default prefix
  rules:
    - match: <host>/<path-prefix>    # required
      target: <url>                  # required
      match_mode: boundary|prefix    # optional; inherits default_match_mode
```

Loopback is exempt from the proxy (`no_proxy=localhost,127.0.0.1,::1`). Match order: exact host equality, then longest path-prefix first, then the rule's match mode, first match wins.

```yaml
pypi_overrides:        # optional; builds wheels on the host, serves them from an
  packages:            # in-container pypiserver via UV_EXTRA_INDEX_URL/PIP_EXTRA_INDEX_URL
    - name: <str>      # required. package name as resolved by pip/uv
      # exactly one of the three following sources:
      wheel_var: <str>     # name of a --var whose value is a wheel path
      wheel_path: <str>    # path on disk, relative to the profile file; glob allowed
      wheel_from_git:
        repo: <url>        # required
        ref: <str>         # default main
        username: <str>    # optional
        token_var: <str>   # optional; injects Basic auth into the clone URL
        build_cmd: <str>   # default "uv run --with maturin maturin build --release"
        wheel_glob: <str>  # default "target/wheels/*.whl"
```

```yaml
passthrough:              # optional
  allow_external: <bool>  # default true
  services:
    - name: <str>         # required. label only
      key_env: <str>      # host env var copied into the container if it exists
```

```yaml
provision:                   # optional
  files:                     # pushed before setup_cmds run
    - src: <path>            # required. relative paths resolve against the profile file
      dest: <abs path>       # required. absolute path inside the container
      recursive: <bool>      # default false. REQUIRED true when src is a directory
      create_dirs: <bool>    # default true
      mode: <str>            # optional, e.g. "0644"
      uid: <int>             # optional
      gid: <int>             # optional
  setup_cmds:                # list of shell command strings, run in order
    - <str>
```

Directory push semantics: with `recursive: true`, `dest` is the **parent**. `src: ./data/` with `dest: /root/app/` lands files at `/root/app/data/...`; `dest: /root/app/data/` lands them at `/root/app/data/data/...`. For a single file, `dest` is the file path.

```yaml
update:                    # optional; consumed by the `update` command
  cmds:                    # required when update is present. run in order
    - <str>
  refresh_pypi: <bool>     # default false. rebuilds pypi_overrides wheels first
```

```yaml
access:                     # optional; creates Incus proxy devices
  hostname: <str>           # optional. registers <name>.local via Avahi
  ports:
    - host: <int>           # required. port on the host
      container: <int>      # required. port inside the container
      label: <str>          # default ""
      path: <str>           # default "/". appended to printed access URLs
      verify: <bool>        # default true
      verify_timeout: <int> # default 30 (seconds)
      verify_interval: <int># default 2 (seconds)
```

```yaml
readiness:                 # optional; a list. evaluated on demand, not at launch
  - name: <str>            # required
    # exactly one of http, tcp, command per entry:
    http:
      url: <url>           # required. runs `curl -sf <url>` inside the container
      expect_json: <map>   # optional. subset match on the JSON body
    tcp:
      port: <int>          # required. connects to localhost:<port> inside the container
    command: <str>         # runs via incus exec; passes on exit code 0
```

```yaml
mock_services:        # optional; Docker sidecars, routed to by real hostname via mitmproxy
  - source: <str>     # required. local dir or git URL containing digital-twin-mock.yaml
    config:           # optional. arbitrary keys -> env vars (uppercased) in the container
      api_key: <str>
```

Variables: any string value may contain `${VAR_NAME}`, supplied at launch with `--var K=V`. Substitution runs across all string values. `localhost` and `127.0.0.1` inside variable values are rewritten to the host gateway IP.

## Conditional requirements

```
base                       -> base.image required
provision.files[]          -> src and dest required
provision.files[] src=dir  -> recursive: true required
url_rewrites               -> rules[].match and rules[].target required
url_rewrites.auth          -> default_match_mode: boundary (credentials leak on over-match)
url_rewrites               -> match_mode/default_match_mode must be "boundary" or "prefix"
pypi_overrides.packages[]  -> name required
pypi_overrides.packages[]  -> exactly one of wheel_var | wheel_path | wheel_from_git
wheel_from_git             -> repo required; repo must fully resolve (unresolved ${VAR} fails launch)
passthrough.services[]     -> name required
access.ports[]             -> host and container required, both must be integers
readiness[]                -> name required
readiness[]                -> exactly one of http | tcp | command
readiness[].http           -> url required
readiness[].tcp            -> port required, must be an integer
update                     -> cmds required
update.refresh_pypi: true  -> pypi_overrides must be defined in the same profile
mock_services[]            -> source required
```

## Authoring rules

Base image: `ubuntu:24.04` unless the target explicitly needs another distribution or a language-specific image.

System dependencies, installed in the first setup command:

```
git curl                       almost always
build-essential                native extensions, C compilation
libssl-dev                     TLS/crypto native builds
nodejs npm                     Node.js projects
postgresql postgresql-contrib  Postgres running inside the container
avahi-daemon avahi-utils       only when access.hostname is set
```

Install method by language:

```
Python (pyproject.toml)  curl -LsSf https://astral.sh/uv/install.sh | sh
                         then uv tool install <pkg>  (applications)
Python from git          uv tool install git+https://github.com/<owner>/<repo>
Python libraries         uv venv /root/app/.venv
                         uv pip install --python /root/app/.venv/bin/python <pkg>
                         then run via /root/app/.venv/bin/<entrypoint>
Node.js                  npm install -g <pkg>   or   npm install && npm start
Rust                     rustup then cargo install <pkg>
Go                       go install <pkg>@<ver>
```

The system interpreter on Ubuntu 24.04 is externally managed (PEP 668). `uv pip install --system` and bare `pip install` both fail with "The interpreter at /usr is externally managed", and the failure aborts the launch. Install applications with `uv tool install`, and libraries into an explicit virtualenv. Never reach for `--break-system-packages`.

A library is not a runnable application. Installing a web framework does not give you a server to start; the profile must also supply the application code that imports it.

Getting source into the DTU:

```
published to PyPI/npm         install from the registry
on GitHub, unmodified         install from git+https://github.com/...
local unpublished changes     push the code to a Gitea instance, then either
                              url_rewrites the upstream URL to Gitea, or install
                              from the Gitea URL directly in setup_cmds
prebuilt wheel on the host    pypi_overrides with wheel_path or wheel_var
built from a repo at launch   pypi_overrides with wheel_from_git
static config or seed data    provision.files, only when the file already
                              exists on the caller's host at that exact path
```

`provision.files` pushes a host path into the container. It fails the launch when the path does not exist. Use it only for files the caller told you they have. For any file you are inventing, including application source, config, and fixtures, write it with a heredoc in `setup_cmds`:

```yaml
- |
  mkdir -p /root/app
  cat > /root/app/server.js << 'EOF'
  <file content>
  EOF
```

Quote the heredoc delimiter as `'EOF'` so the shell does not expand `$` inside the body.

Ports: choose `host` from 30000-39999. Proxy device creation does not collision-check, so a port already bound on the Incus host silently fails to expose. `container` is whatever the application natively listens on; leave it at the app's default.

Readiness check per application shape:

```
HTTP server with a health endpoint   http: url + expect_json
HTTP server without one              http: url pointing at the root path
database, broker, non-HTTP listener  tcp: port
CLI tool, no listener                command: <tool> --version
CLI tool with a runtime dependency   command that exercises the dependency end to end
provisioning side effect             command: test -f /tmp/<marker>
```

For a CLI whose first real invocation triggers a lazy install or a cold cache, add a warm-up command at the end of `setup_cmds` that performs that invocation once. Readiness commands run under a 30 second timeout and will otherwise race the cold path.

Servers must be started in the background from `setup_cmds`, since launch waits for each command to exit:

```yaml
- |
  nohup <start command> > /var/log/<app>.log 2>&1 &
  sleep 1
```

Add `update` when the user will iterate on code and re-provision in place. An update block for a server stops the old process, reinstalls, and restarts it. Use `pkill -f '[a]pp-name'` so the pattern does not match its own command line, and `|| true` so a not-running process does not fail the update.

Add `passthrough.services` for every API key the software needs. Reference the forwarded variable directly in later setup commands; it is exported before they run.

## Rewrite companion endpoints

A `url_rewrites` rule for a git host is frequently not sufficient by itself. Many installers resolve refs or fetch metadata from *different hosts* before ever issuing a `git fetch`. Miss those and the installer pins to the upstream SHA, then git-fetches that SHA through the proxy, which succeeds because the mirror was seeded from upstream. The install completes at the wrong commit with no error.

For every rewrite rule, work through the toolchain that will consume it:

1. Which installer in `setup_cmds` consumes this URL (uv, pip, npm, go, cargo, plain git)?
2. Does it resolve `@<ref>` to a SHA out of band before the fetch, and from which host?
3. Does it pull manifests (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`) from a CDN instead of cloning, and from which host?
4. Does it pull tarballs or archives from a host other than the git URL?

Per-toolchain patterns, not a closed list:

```
uv tool install git+https://github.com/...
    resolves SHAs via api.github.com/repos/<owner>/<repo>/commits/<ref>
    fetches pyproject.toml from raw.githubusercontent.com
    may hit codeload.github.com for archives
    suppress entirely with allow_uv_github_fast_path: false (the default)

npm install <git-url>
    may resolve through codeload.github.com for tarball shortcuts

go get / go mod
    uses proxy.golang.org and sum.golang.org unless GOPRIVATE excludes the host

pip install git+https://...
    plain git clone, no fast path, no companion hosts

cargo install
    uses index.crates.io and static.crates.io
```

For each companion host identified, add a rewrite rule for it. When the toolchain's fast paths cannot be reasoned about confidently, either set that toolchain's fast-path-disable env var in the container, or pre-resolve the mirror's HEAD SHA and pass the literal SHA to the install command instead of a branch name. A literal SHA skips out-of-band resolution entirely.

## Command style

Every command path (`setup_cmds`, `update.cmds`, `readiness.command`, `exec`) is wrapped in `bash -lc`. That login shell sources `/etc/profile.d/dtu-env.sh`, which puts `/root/.cargo/bin:/root/.local/bin` on PATH and exports passthrough env vars. Anything installed by `uv tool install`, `cargo install`, or `npm install -g` resolves by bare name.

Write commands bare:

```yaml
- amplifier --version
```

Do not add any of the following:

```
bash -lc '...' wrapping                            double-wraps
bash -c 'export PATH="/root/.local/bin:$PATH" && cmd'
PATH=/root/.local/bin:$PATH cmd                    prefix on readiness.command
/root/.local/bin/<tool>                            hardcoded absolute tool paths
export PATH="/root/.local/bin:$PATH"               at the top of a heredoc block
```

Use `command -v <tool>` for existence checks rather than testing an absolute path. Shell constructs (pipes, redirects, `&&`, `||`, heredocs, background jobs) work directly inside a single command string; the outer wrap already provides a shell. Multi-line commands use a YAML block scalar (`- |`).

## Common mistakes

```
Unknown field
    Dropped with a warning, never an error. A misspelled key silently loses
    the setting. Emit only keys that appear in the schema above.

prefix instead of boundary
    A prefix rule matching github.com/org/repo also captures org/repo-foundation,
    org/repo-module-x, and every other sibling sharing the prefix. Always set
    default_match_mode: boundary on any url_rewrites block that rewrites
    repository URLs. With auth present, an over-match sends the credential to
    the wrong target. The loader warns via SuspiciousPrefixRuleWarning and
    OverlappingRewriteRulesWarning but does not raise.

Unresolved ${VAR} in an integer field
    access.ports[].host, access.ports[].container, readiness[].tcp.port,
    verify_timeout, verify_interval, uid and gid are coerced with int().
    An unsubstituted ${VAR} raises at load. Use literal integers.

Unresolved ${VAR} elsewhere
    In url_rewrites.rules[].target it causes proxy setup to be skipped
    silently, so no rewriting happens at all. In
    pypi_overrides.packages[].wheel_from_git.repo it fails the launch.
    Every ${VAR} emitted must be one the user will pass with --var, and the
    profile's leading comment block must list them.

setup_cmds ordering
    Commands run in order and launch aborts on the first non-zero exit.
    Order is: system deps, then language toolchain, then the project install,
    then config files, then background start, then verification. Never
    reference a tool before the command that installs it.

recursive missing on a directory push
    provision.files with a directory src and no recursive: true fails to
    transfer. It defaults to false.

Directory destination off by one level
    With recursive: true, dest is the parent and the source basename is
    preserved inside it.

More than one of an exactly-one-of set
    pypi_overrides packages take exactly one of wheel_var, wheel_path,
    wheel_from_git. Readiness checks take exactly one of http, tcp, command.
    Zero also raises; the count must be exactly one.

Foreground server start
    A start command without nohup and & blocks the launch forever.

refresh_pypi without pypi_overrides
    update.refresh_pypi: true requires a pypi_overrides section in the same
    profile.
```

## Examples

Reach for this shape for a CLI tool with no listening port: install, configure, verify.

```yaml
# Standalone Amplifier user environment.
#
# Installs Amplifier from upstream with the amplifier-foundation bundle
# composed onto every session, so a user can `exec` in and run an interactive
# `amplifier` session immediately.
#
# Intended for interactive use via --visual-id, e.g.:
#   amplifier-digital-twin launch profiles/amplifier/amplifier-standalone.yaml
#   amplifier-digital-twin exec <id> --visual-id amplifier
name: amplifier-standalone
description: >
  Standalone Amplifier user environment with the foundation bundle composed,
  ready for interactive `amplifier` sessions via exec.
base:
  image: ubuntu:24.04

passthrough:
  allow_external: true
  services:
    - name: anthropic
      key_env: ANTHROPIC_API_KEY

provision:
  setup_cmds:
    # System deps
    - apt-get update && apt-get install -y git curl

    # Install uv
    - curl -LsSf https://astral.sh/uv/install.sh | sh

    # Install Amplifier from upstream
    - uv tool install -vv git+https://github.com/microsoft/amplifier

    # Compose the foundation bundle onto every session
    - amplifier bundle add git+https://github.com/microsoft/amplifier-foundation@main --app

    # Configure the Anthropic provider so sessions can reach the LLM.
    # $ANTHROPIC_API_KEY is forwarded via passthrough.services.
    - |
      mkdir -p /root/.amplifier
      cat > /root/.amplifier/settings.yaml << EOF
      config:
        providers:
          - module: provider-anthropic
            source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
            config:
              api_key: $ANTHROPIC_API_KEY
      EOF

    # Sanity check
    - amplifier --version
```

Reach for this shape for a web server: forwarded port, backgrounded start, HTTP readiness, and an update block that restarts the process.

```yaml
# Amplifier Chat web UI backed by amplifierd.
# All dependencies fetched from upstream GitHub and PyPI.
name: amplifier-chat
description: >
  Amplifier Chat web UI backed by amplifierd.
  All dependencies fetched from upstream.
base:
  image: ubuntu:24.04

access:
  ports:
    - host: 8410
      container: 8410
      label: Chat UI
      path: /chat/

passthrough:
  allow_external: true
  services:
    - name: anthropic
      key_env: ANTHROPIC_API_KEY

provision:
  setup_cmds:
    # System deps
    - apt-get update && apt-get install -y git curl

    # Install uv
    - curl -LsSf https://astral.sh/uv/install.sh | sh

    # Install amplifier-chat (standalone extra pulls in amplifierd).
    - uv tool install "amplifier-chat[standalone] @ git+https://github.com/microsoft/amplifier-chat"

    # Configure Amplifier provider so sessions can reach the LLM.
    # $ANTHROPIC_API_KEY is available via passthrough env forwarding.
    - |
      mkdir -p /root/.amplifier
      cat > /root/.amplifier/settings.yaml << EOF
      config:
        providers:
          - module: provider-anthropic
            source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
            config:
              api_key: $ANTHROPIC_API_KEY
      EOF

    # Start the server in background
    - |
      nohup amplifier-chat --host 0.0.0.0 --port 8410 --no-browser \
        > /var/log/amplifier-chat.log 2>&1 &
      sleep 1

update:
  cmds:
    # [a] trick prevents pkill from matching its own bash -lc command line.
    - pkill -f '[a]mplifier-chat' || true
    # Re-install from upstream to pick up latest code.
    - uv tool install --reinstall --force "amplifier-chat[standalone] @ git+https://github.com/microsoft/amplifier-chat"
    - |
      nohup amplifier-chat --host 0.0.0.0 --port 8410 --no-browser \
        > /var/log/amplifier-chat.log 2>&1 &
      sleep 1

readiness:
  - name: amplifierd-ready
    http:
      url: http://localhost:8410/ready
      expect_json: { "ready": true }
```

Reach for this shape when local unpublished changes must replace upstream dependencies: repo URLs redirected to a mirror, and a package name served from a locally built wheel.

```yaml
# Simulating an Amplifier user's experience after making local changes to amplifier-core and amplifier-module-provider-anthropic
# Amplifier is installed and run as a user would.
#
# amplifier-core is typically installed by amplifier from PyPI so we override with our built wheel.
# amplifier-module-provider-anthropic is served through an external Gitea instance
# (managed via amplifier-bundle-gitea) while all other dependencies come from upstream GitHub and PyPI.
#
#
# Variables (provided at launch via --var):
#   GITEA_URL              -- Gitea base URL reachable from inside the
#                             environment (e.g. http://10.0.0.1:10110)
#   GITEA_TOKEN            -- API token for Gitea
name: amplifier-user-sim
description: >
  Simulating an Amplifier user's experience after making local changes to amplifier-core and amplifier-module-provider-anthropic
base:
  image: ubuntu:24.04

# URL rewriting rules.
url_rewrites:
  auth:
    username: admin
    token_var: GITEA_TOKEN
  # Keep uv's GitHub fast path disabled so `uv tool install` routes through
  # git fetch and these rules actually apply. Set to true only when you
  # specifically want to observe uv's native behavior (the fast path bypasses
  # url_rewrites).
  allow_uv_github_fast_path: false
  default_match_mode: boundary
  rules:
    - match: github.com/microsoft/amplifier-module-provider-anthropic
      target: ${GITEA_URL}/admin/amplifier-module-provider-anthropic

# PyPI overrides.
#
# A HTTPS proxy intercepts package-index requests for
# these packages and redirects them to the index in Digital Twin that is hosted the modified version.
pypi_overrides:
  packages:
    - name: amplifier-core
      wheel_from_git:
        repo: ${GITEA_URL}/admin/amplifier-core.git
        ref: main
        username: admin
        token_var: GITEA_TOKEN
        build_cmd: uv run --with maturin maturin build --release
        wheel_glob: target/wheels/amplifier_core-*.whl

passthrough:
  allow_external: true
  services:
    - name: anthropic
      key_env: ANTHROPIC_API_KEY

provision:
  setup_cmds:
    # Core tooling
    - apt-get update && apt-get install -y git curl
    - curl -LsSf https://astral.sh/uv/install.sh | sh

    # Install Amplifier. amplifier-core is resolved from the local
    # simple index / pypiserver override; everything else comes from
    # the normal upstream GitHub / package index sources.
    - uv tool install -vv git+https://github.com/microsoft/amplifier

    # Configure Amplifier provider so the first-run wizard is skipped.
    # $ANTHROPIC_API_KEY is available via passthrough env forwarding.
    - |
      mkdir -p /root/.amplifier
      cat > /root/.amplifier/settings.yaml << EOF
      config:
        providers:
          - module: provider-anthropic
            source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
            config:
              api_key: $ANTHROPIC_API_KEY
      EOF

    # Validation
    - amplifier --version

    # Workspace -- empty dir where a user can launch Amplifier
    - mkdir -p /home/user/project

    # Warm-up: install provider modules now so the first readiness smoke
    # below doesn't hit the 30s command timeout on a cold cache.
    - 'amplifier run "Say exactly: warmup-ok"'

update:
  refresh_pypi: true
  cmds:
    # Force a clean re-resolve from the (rewritten) URLs. `amplifier reset
    # --remove cache -y` clears the cache AND reinstalls, so the editable
    # provider installs are re-created -- unlike `rm -rf ~/.amplifier/cache`,
    # which deletes the trees those installs point into and leaves them
    # dangling ("No providers available").
    - amplifier reset --remove cache -y
    - uv tool install --reinstall --force git+https://github.com/microsoft/amplifier
    # Warm-up: trigger the lazy provider install now so the post-update
    # readiness smoke doesn't race the cold install and hit the 30s timeout.
    - 'amplifier run "Say exactly: warmup-ok"'

readiness:
  - name: amplifier-installed
    command: amplifier --version
  - name: providers-smoke
    command: 'amplifier run "Say exactly: dtu-ready"'
```

## Output contract

Emit exactly one YAML document inside a single fenced code block whose info string is `yaml`. Nothing before it, nothing after it: no preamble, no explanation, no summary, no follow-up questions.

The document opens with a `#` comment block that states what the environment provides, how it is meant to be used, and one line per `${VAR}` naming the variable and what value it takes. Omit the variables list when the profile has no variables. Follow the comment block with `name:` and `description:`, then the remaining sections.

Carry all reasoning into inline `#` comments on the lines they explain: why a rewrite rule exists, why a command is ordered where it is, what a background start is doing, what a warm-up command prevents.
