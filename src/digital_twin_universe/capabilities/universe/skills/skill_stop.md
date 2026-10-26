Stop work in an owned universe without removing its containers, volumes, or management state.

```bash
digital-twin-universe stop --id <universe-id> --timeout-seconds 10
```

## Arguments

- `--docker-host URI`: the original Docker endpoint. Required for legacy records without a pinned endpoint.
  Library callers pass `config=BackendConfig(docker_host=...)`. This selects the endpoint for this call only;
  the record is not adopted or changed. An explicit endpoint that differs from a pinned record is refused.

- `--id`: exact universe id returned by launch.
- `--timeout-seconds`: shutdown grace period, from 1 to 300 seconds; default 10.

## Result

`Stopped` JSON reports `stopped`, `incomplete`, or `unknown`, with containers still running and any errors.
Exit 0 confirms no owned container is running; exit 1 means cessation was not confirmed.
Stop does not resume or replay commands. Use `destroy` only when the retained environment and its data should be removed.
