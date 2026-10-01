`check` reports whether this host can run a universe: the `docker` CLI on `PATH`, a daemon
that answers it, and the Compose plugin. It probes them in that order and stops at the first
one missing, so a report never claims a daemon behind a missing CLI. Nothing on the host
changes.

Run it before anything that needs Docker: `launch`, `create-profile`, and every command on a
running universe. When it exits 1, each missing prerequisite carries a `remedy`; `install`
plans the fix.

```bash
digital-twin-universe check

# gate a script on it
digital-twin-universe check > /dev/null || digital-twin-universe install
```

```python
from digital_twin_universe.lib import check

report = check()
report.ok, [missing.remedy for missing in report.prerequisites if not missing.present]
```

## Arguments

None.

## Result

`HostReport` JSON on stdout:

- `ok`: true when every prerequisite is present.
- `platform`: `linux`, `macos`, or `windows`. Inside WSL it is `linux`.
- `docker_version` and `compose_version`: the daemon's server version and the Compose plugin's
  version, or null when that probe failed or never ran.
- `prerequisites`: one entry per probe that ran, in order `docker-cli`, `docker-daemon`,
  `docker-compose`, so it holds fewer than three when one is missing. Each has `name`,
  `present`, `detail`, and `remedy`. `detail` is the CLI's path or the version when present,
  and otherwise what the probe saw: `` `docker` is not on PATH `` or the first line Docker
  printed, such as a permission error on its socket. `remedy` is null when present; otherwise
  it says to run `digital-twin-universe install`, then `install --yes`, or to follow Docker's
  install page for this platform, plus the Compose plugin page when Compose is the one missing.

## Failures

Exits 0 when `ok` is true and 1 when it is not, with the report on stdout either way. Any
argument is a bad invocation and exits 2. It raises no `DigitalTwinUniverseError`: a missing
prerequisite is part of the report, not a failure. On an operating system other than Linux,
macOS, or Windows it raises `RuntimeError` naming the system.
