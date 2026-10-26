`status` measures one universe against Docker now: its state, each service's state and health,
and the URLs the host can open. It changes nothing.

Use it before `exec` when time has passed since `launch`, to check a universe is still
`running`, or to get its URLs again. For every universe at once, use `list`.

```bash
digital-twin-universe status --id dtu-hello-3f2a
```

```python
from digital_twin_universe.lib import status

universe = status("dtu-hello-3f2a")
universe.state, [(service.name, service.health) for service in universe.services], universe.urls
```

## Arguments

- `--docker-host URI`: the original Docker endpoint. Required for legacy records without a pinned endpoint.
  Library callers pass `config=BackendConfig(docker_host=...)`. This selects the endpoint for this call only;
  the record is not adopted or changed. An explicit endpoint that differs from a pinned record is refused.

- `--id TEXT`: the universe id, the `id` field `launch` printed, such as `dtu-hello-3f2a`.
  Required. A container or image name such as `dtu-hello-3f2a-twin-1` is not an id.

## Result

`Universe` JSON on stdout, the shape `launch` prints; `digital-twin-universe launch --help`
describes every field. `state` is `running` when every service is up and healthy, `starting`
while a healthcheck is still starting, `degraded` when any service is not running or is
unhealthy, and `stopped` when none is running, including when its containers were removed
outside the tool.
