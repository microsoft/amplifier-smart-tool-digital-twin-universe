`validate-profile` checks a profile without launching anything: Compose reads it through
`docker compose config`, then the tool checks the `x-dtu` block, the rules every universe has to
follow, and how realistic the twin is. It starts no containers and writes nothing.

Use it after writing or editing a profile, before `launch`, to get every problem at once with a
remedy for each. `launch` runs the same checks and refuses a profile with errors, so for a
profile you trust, `launch` alone is enough. It still needs Docker: Compose does the reading.

```bash
digital-twin-universe validate-profile --profile .agents/digital-twin-universe/my-app

# a shipped example, by name
digital-twin-universe validate-profile --profile web-site

# launch only what validates
digital-twin-universe validate-profile --profile my-app && \
  digital-twin-universe launch --profile my-app
```

```python
from digital_twin_universe.lib import validate_profile

report = validate_profile("my-app")
report.ok, [(finding.code, finding.location) for finding in report.errors], report.warnings
```

## How `--profile` resolves

In this order:

1. An existing file is the Compose file.
2. An existing directory must hold `compose.yaml` or `docker-compose.yaml`, in that order.
3. Anything else with a path separator in it fails with `profile-not-found`.
4. A bare name is looked for as `.agents/digital-twin-universe/<name>/`, from the working
   directory up to and including the git root, then among the examples shipped under
   `examples/`.

A directory beside you with the same name as a profile wins over the profile, because it is an
existing directory.

The twin is the service named by `x-dtu.twin_machine`, else the only service, else the service
called `twin`.

## Errors and warnings

An error means the profile is not a universe and `launch` will refuse it. A warning means the
universe would be less realistic than it probably means to be; it never changes `ok` or the exit
code, and it may be exactly what the author wanted.

Error codes:

- `profile-invalid`: Compose rejected the file; the message is Compose's own. When this or
  `env-missing` appears, it is the only finding, since nothing else could be read.
- `env-missing`: the file reads a variable with `${NAME:?...}` and it is not set. Export it
  and validate again; it is read at launch and never written to a file.
- `x-dtu-invalid`: a key in `x-dtu` is unknown or has the wrong type. One finding per problem.
- `twin-missing`: no service matches the twin rule above.
- `twin-excluded-by-compose-profile`: the twin exists but a Compose `profiles:` entry gates it,
  so it would not start.
- `reserved-service-name`: a service is called `git` or `gateway`, which the tool's overlay
  uses.
- `mixed-platforms`: Windows and Linux services in one file.
- `repository-not-git`: an `x-dtu.repositories` path, relative to the Compose file's directory,
  is not a git repository.
- `repository-url-invalid`: a repository `url` is not `http` or `https` with a host and a path.
- `url-port-unpublished`: an `x-dtu.urls` port is not a TCP port the twin publishes.
- `routes-around-gateway`: with rewrites, `allow`, or a repository `url` present, a service sets
  `network_mode`, `dns`, `extra_hosts`, or a network other than `default`.

Warning codes, all about the twin except the last:

- `no-long-running-command`: no `command` or `entrypoint`, so it runs whatever its image does.
- `bind-mount`: it mounts a host path it can change.
- `no-healthcheck`: `launch` cannot tell when it is ready.
- `runs-as-root`: `user` is `root`, `0`, `root:root`, or `0:0`.
- `url-host-unresolved`: an `x-dtu.urls` host is neither `localhost`, `*.localhost`, nor a name
  that resolves to loopback here.

## Arguments

- `--profile TEXT`: a profile name, a Compose file, or a directory holding one, resolved as
  above. Required. The library also takes a `Path`.

## Result

`ProfileReport` JSON on stdout, whether or not it passes.

- `path`: the resolved Compose file.
- `name`: the Compose project name, else the name of the directory holding the file.
- `twin_machine`: the twin's service, or empty when no twin was found.
- `services`: every service Compose would start.
- `ok`: true when `errors` is empty.
- `errors` and `warnings`: findings, each with `code`, `location` (such as
  `services.twin.volumes[0]` or `x-dtu.urls[1].port`, or null for the whole file), `message`,
  and `remedy`.

## Failures

Exits 0 when `ok` is true and 1 when it is not, with the report on stdout either way. Exits 2 on
a bad invocation, such as a missing `--profile`.

When there is no profile to report on, it prints `message` and `remedy` to stderr and exits 1.
The library raises `DigitalTwinUniverseError` with `code`:

- `profile-not-found`: the path does not exist, a directory holds no Compose file, or no
  profile has that name; the message lists every place it looked.
- `docker-unavailable`: Docker is not usable; run `digital-twin-universe check`.
