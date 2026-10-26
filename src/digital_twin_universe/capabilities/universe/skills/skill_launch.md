`launch` validates a profile, records a new universe, brings it up with `docker compose up
--build --wait`, and returns once every service is running and every healthcheck passes. The
result carries the universe id that `exec`, `file-push`, `file-pull`, `status`, and `destroy`
take.

Use it whenever you need the twin running. Each call launches a new universe with a new id, even
from the same profile; reuse a running one from `list` instead of launching twice. The universe
keeps running after this command exits, until `destroy`. Destroy what you launch.

The commands below are examples. `hello`, `copilot-cli`, and `amplifier-modules-dtu` are profiles
shipped with the tool, in `examples/` under the skill directory, so those names launch from any
working directory once their variables are set. `my-app` and the `~/code/...` paths stand for your
own profile and checkouts.

```bash
digital-twin-universe launch --profile hello

# a profile in this project, given a long first build
digital-twin-universe launch \
  --profile .agents/digital-twin-universe/my-app \
  --timeout-seconds 1200

# a profile that reads a host variable at launch
export GH_TOKEN="$(gh auth token)"
digital-twin-universe launch --profile copilot-cli

# Amplifier from GitHub, with local checkouts served in place of its repositories; the Rust build is long
export DTU_AMPLIFIER_CORE=~/code/amplifier-core DTU_PROVIDER_ANTHROPIC=~/code/amplifier-module-provider-anthropic
digital-twin-universe launch --profile amplifier-modules-dtu --timeout-seconds 1200
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
unchanged one quick. With the gateway, Buildx builds them first, with
`docker buildx bake --allow=network.host` from `bake.yaml` in the same directory, and Compose
only starts them.

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
