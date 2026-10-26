# Export files

Save files from the exact owned twin as a private tar archive, including after Stop. This is deterministic and needs no model provider.
Bounded pipe handling currently requires Linux, WSL, or macOS; native Windows receives `export-unsupported` before copying.

```bash
digital-twin-universe export-files --id <universe-id> --source /workspace --destination ./workspace.tar
```

## Arguments

`--id` is the owned universe id, `--source` is an absolute container path, and `--destination` is a new archive path whose directory exists.
`--docker-host URI` explicitly selects the original Docker endpoint for a legacy record without a pinned endpoint. It does not adopt or persist that choice; a pinned endpoint mismatch is rejected.
The default limit is 256 MiB and 60 seconds; use `--max-bytes` up to 16 GiB and `--timeout-seconds` up to 3600.

## Result

The result gives the archive path, byte count and SHA-256. Existing destinations are never overwritten. The archive is not extracted on the host; treat its contents as untrusted when opening it elsewhere.
Export source and artifacts before destroying an environment whose files must be retained. This operation does not remove containers or volumes.
