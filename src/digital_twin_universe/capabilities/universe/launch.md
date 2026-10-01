`launch` validates a profile, records a new universe, brings it up with `docker compose up
--build --wait`, and returns once every service is running and every healthcheck passes. The
result carries the universe id that `exec`, `file-push`, `file-pull`, `status`, and `destroy`
take.

Use it whenever you need the twin running. Each call launches a new universe with a new id, even
from the same profile; reuse a running one from `list` instead of launching twice. The universe
keeps running after this command exits, until `destroy`. Destroy what you launch.

```bash
digital-twin-universe launch --profile hello

# a profile in this project, given a long first build
digital-twin-universe launch \
  --profile .agents/digital-twin-universe/my-app \
  --timeout-seconds 1200

# a profile that reads a host variable at launch
export GH_TOKEN="$(gh auth token)"
digital-twin-universe launch --profile copilot-cli
```

```python
from digital_twin_universe.lib import destroy, execute, launch

universe = launch("hello")
try:
    execute(universe.id, "echo dtu-ok").stdout
finally:
    destroy(universe.id)
```

`--profile` resolves exactly as in `validate-profile`: an existing file, then an existing
directory holding `compose.yaml` or `docker-compose.yaml`, then a bare name looked for under
`.agents/digital-twin-universe/<name>/` from the working directory up to the git root, then
among the shipped examples. `digital-twin-universe validate-profile --help` has the details and
every finding code.

## What a launch leaves behind

The id is `dtu-<profile name>-<4 hex digits>`. It is also the Compose project name, so
`docker compose -p <id> ps` and `logs` reach the same stack, and container names start with it.
Pass the id itself, not a container name, to the other commands.

`~/.digital-twin-universe/universes/<id>/` holds `universe.json`, the record that leads an id
back to the universe. A profile with `x-dtu.repositories`, `rewrites`, or `allow` also gets
`dtu.yaml` there: an overlay adding a `git` service that serves the repositories and a
`gateway` service every request goes through, started first. The record is written before
anything starts, so a launch that fails after that point leaves a universe to `destroy`.

Every service with a `build:` is rebuilt on every launch; Docker's build cache makes an
unchanged one quick.

## Arguments

- `--profile TEXT`: a profile name, a Compose file, or a directory holding one. Required. The
  library also takes a `Path`.
- `--timeout-seconds N`: how long Compose waits for services to become healthy. Defaults to 600.
  A profile with an overlay is brought up in two passes, the overlay's services first, and each
  pass gets the full timeout.

## Result

`Universe` JSON on stdout. Compose's build and startup progress streams to stderr.

- `id`: the universe id.
- `name` and `description`: the profile's Compose name and `x-dtu.description`.
- `profile_path`: the Compose file it was launched from.
- `twin_machine`: the service the software under test runs in, the one `exec` enters.
- `state`: `running` when every service is up and healthy, `starting` while a healthcheck is
  still starting, `degraded` when any service is not running or is unhealthy, `stopped` when
  none is running. A successful launch normally reports `running`.
- `services`: each with `name`, `state` (Compose's word, such as `running` or `exited`),
  `health` (`starting`, `healthy`, `unhealthy`, or null without a healthcheck), and `image`.
- `urls`: the twin's published TCP ports as the host opens them, in host port order, each with
  `url`, `port` (the host port), `path`, and `label`. A port listed in `x-dtu.urls` gets one
  entry per listing, with its host, path, and label; any other gets `http://localhost:<port>/`.
  Ports of other services are not reported.
- `state_path`: the universe's directory under `~/.digital-twin-universe/universes/`.
- `created_at`: when it was launched, in UTC.

## Failures

Prints `message` and `remedy` to stderr and exits 1. Exits 2 on a bad invocation, such as a
missing `--profile`. The library raises `DigitalTwinUniverseError` with `code`.

Nothing is recorded or started for these:

- `profile-not-found`: the path does not exist, a directory holds no Compose file, or no
  profile has that name.
- `profile-invalid`: the profile has validation errors, each listed with its code, or Compose
  rejected the file.
- `env-missing`: the profile reads a host variable that is not set. Export it and launch again.
- `docker-unavailable`: Docker is not usable; run `digital-twin-universe check`. It can also
  happen after the record is written.

These leave the universe recorded, with whatever started still up, for inspection with
`docker compose -p <id> ps` and `logs`. The message names the id; `destroy --id <id>` clears it:

- `port-in-use`: a host port in the profile's `ports` is already held.
- `build-failed`: an image build failed; the message has the end of the build output.
- `unhealthy`: a container failed its healthcheck; the message has its last log lines.
- `timeout`: services were still starting after `--timeout-seconds`.
- `launch-failed`: `docker compose up` failed some other way; the message has Compose's output.
