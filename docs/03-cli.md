# CLI Reference

The CLI is a thin wrapper over the [library](01-library.md). Each command maps
to one library capability; this page documents only the command-line surface:
flags, defaults, output, and exit behavior. `-h` prints the terse summary for a
person; `--help` prints the complete listing for an agent, and
`<capability> --help` prints everything needed to call that one capability.

## manifest

```bash
amplifier-digital-twin-universe manifest
```

No flags. Deterministic.

## check

```bash
amplifier-digital-twin-universe check
```

No flags. Deterministic. Reads only; nothing is installed, configured, or
started.

## validate-profile

```bash
amplifier-digital-twin-universe validate-profile --file profile.yaml
cat profile.yaml | amplifier-digital-twin-universe validate-profile --file -
amplifier-digital-twin-universe validate-profile --file profile.yaml --var PORT=8000
```

- `--file PATH|-`: path to the profile YAML, or `-` for stdin. Required.
- `--var KEY=VALUE`: launch variable. Repeatable.

Deterministic.

## launch

```bash
amplifier-digital-twin-universe launch --profile profile.yaml
amplifier-digital-twin-universe launch --profile profile.yaml --var PORT=8000 --name my-env
amplifier-digital-twin-universe launch --profile profile.yaml --hostname my-env --max-environments 0
```

- `--profile PATH`: path to the profile YAML to launch. Required.
- `--var KEY=VALUE`: launch variable. Repeatable.
- `--name NAME`: name the environment instead of generating one.
- `--hostname NAME`: register `NAME.local` for the environment over mDNS.
- `--max-environments N`: refuse to launch beyond N concurrent environments.
  `0` removes the ceiling.

Deterministic. Requires `incus`. Blocks until provisioning finishes.

## exec

```bash
amplifier-digital-twin-universe exec --id dtu-1a2b3c4d --command "curl -sf localhost:8000/health"
amplifier-digital-twin-universe exec --id dtu-1a2b3c4d --command "uname -a" --timeout 30
amplifier-digital-twin-universe exec --id dtu-1a2b3c4d --command "long-running-job" --timeout none
```

- `--id ID`: the environment id, as reported by `list`. Required.
- `--command TEXT`: one shell command line, split into arguments and run
  under a login shell inside the environment. Required.
- `--timeout SECS|none`: seconds to allow, or `none`. Default 600.

Deterministic. Requires `incus`. For an interactive shell, use the engine's
own `amplifier-digital-twin exec` instead.

## status

```bash
amplifier-digital-twin-universe status --id dtu-1a2b3c4d
```

- `--id ID`: the environment id. Required.

Deterministic. Requires `incus`.

## list

```bash
amplifier-digital-twin-universe list
```

No flags. Deterministic. Requires `incus`. Scoped to the machine, not to a
session.

## check-readiness

```bash
amplifier-digital-twin-universe check-readiness --id dtu-1a2b3c4d
amplifier-digital-twin-universe check-readiness --id dtu-1a2b3c4d --skip-access-check
```

- `--id ID`: the environment id. Required.
- `--skip-access-check`: evaluate only the in-environment checks, not
  host-side port reachability.

Deterministic. Requires `incus`. `ready` is null when the profile declares no
checks, which is not the same as failing them.

## update

```bash
amplifier-digital-twin-universe update --id dtu-1a2b3c4d
amplifier-digital-twin-universe update --id dtu-1a2b3c4d --var PORT=8001 --skip-readiness
```

- `--id ID`: the environment id. Required.
- `--var KEY=VALUE`: launch variable. Repeatable.
- `--skip-readiness`: do not re-run readiness checks afterward.

Deterministic. Requires `incus`. Commands come from the profile snapshot
taken at launch, not the host copy.

## file-push

```bash
amplifier-digital-twin-universe file-push --id dtu-1a2b3c4d --source ./src --destination /workspace --recursive
amplifier-digital-twin-universe file-push --id dtu-1a2b3c4d --source ./a.txt --source ./b.txt --destination /tmp
```

- `--id ID`: the environment id. Required.
- `--source PATH`: host path. Required, repeatable.
- `--destination PATH`: path inside the environment. Required.
- `--recursive`: copy directories and their contents.
- `--mode MODE`: permission bits to set, such as `0644`.
- `--uid UID`: owner to set inside the environment.
- `--gid GID`: group to set inside the environment.
- `--timeout SECS`: seconds to allow per transfer. Default 120.

Deterministic. Requires `incus`. A directory source keeps its own name under
the destination, as `cp -r` does.

## file-pull

```bash
amplifier-digital-twin-universe file-pull --id dtu-1a2b3c4d --source /var/log/app.log --destination ./
amplifier-digital-twin-universe file-pull --id dtu-1a2b3c4d --source /workspace --destination ./out --recursive
```

- `--id ID`: the environment id. Required.
- `--source PATH`: path inside. Required, repeatable.
- `--destination PATH`: host path to write to. Required.
- `--recursive`: copy directories and their contents.
- `--timeout SECS`: seconds to allow per transfer. Default 120.

Deterministic. Requires `incus`. Environments are ephemeral; anything worth
keeping leaves this way before `destroy`.

## destroy

```bash
amplifier-digital-twin-universe destroy --id dtu-1a2b3c4d
```

- `--id ID`: the environment id. Required.

Deterministic. Requires `incus`. Nothing inside survives.

## create-profile

```bash
amplifier-digital-twin-universe create-profile \
  --description "a FastAPI app on port 8000 with a /health endpoint" --out profile.yaml
amplifier-digital-twin-universe create-profile --description "a Postgres-backed API" \
  --context-file notes.md --var PORT=8000 --max-attempts 5 --provider anthropic
```

- `--description TEXT`: what you want to stand up and test. Required.
- `--context-file PATH`: extra material for the model, read into the
  payload.
- `--var KEY=VALUE`: launch variable the drafted profile may reference.
  Repeatable.
- `--out PATH`: write the profile YAML here and report the path.
- `--max-attempts N`: draft-and-repair budget. Default 3.
- `--provider NAME`: model provider to use.
- `--model NAME`: model to use.

Model-backed. Consumes tokens and may return a different answer on a second
run. Each draft is validated by the engine's own profile loader and repaired
until it parses cleanly or the budget is spent.

## install

```bash
amplifier-digital-twin-universe install --goal "test a web service"
amplifier-digital-twin-universe install --context-file notes.md --model claude-sonnet-5
```

- `--goal TEXT`: what you want to use the environments for.
- `--context-file PATH`: extra material for the model, read into the
  payload.
- `--max-attempts N`: draft-and-repair budget. Default 3.
- `--provider NAME`: model provider to use.
- `--model NAME`: model to use.

Model-backed. Proposes only; nothing is installed, configured, or started.
The host evidence the plan was built from is returned with it.

## doctor

```bash
amplifier-digital-twin-universe doctor --symptom "the container has no outbound network"
amplifier-digital-twin-universe doctor --id dtu-1a2b3c4d --symptom "the health check never passes"
```

- `--symptom TEXT`: what you observed, in your own words.
- `--id ID`: measure this environment as well as the host.
- `--context-file PATH`: extra material for the model, read into the
  payload.
- `--max-attempts N`: draft-and-repair budget. Default 3.
- `--provider NAME`: model provider to use.
- `--model NAME`: model to use.

Model-backed. Evidence is measured before the model sees anything and
returned with the diagnosis. Repairs nothing.

## manage

```bash
amplifier-digital-twin-universe manage --request "tear down every stopped environment"
amplifier-digital-twin-universe manage --request "tear down every stopped environment" --confirmed
```

- `--request TEXT`: what you want done, in your own words. Required.
- `--confirmed`: run the planned steps. Authorizes this invocation only.
- `--max-attempts N`: draft-and-repair budget. Default 3.
- `--provider NAME`: model provider to use.
- `--model NAME`: model to use.

Model-backed. Planning changes nothing. A plan that mutates anything runs
only with `--confirmed`. Every step is one of this tool's own deterministic
capabilities, validated before it runs.

## Output

Every capability writes exactly one JSON document to stdout:
`{"result": ...}` on success, or `{"error": {"code", "message", "remedy"}}`
on failure. Progress narration goes to stderr, where humans read it.

A failure nothing anticipated is still an envelope: code `unexpected`, exit 5,
with the traceback on stderr.

## Exit codes

```
0  success
2  bad invocation
3  no model provider configured
4  missing prerequisite
5  the capability ran and failed
```

Per-capability deviations:

- `manifest` and `check` never exit 3 or 4; 5 means the manifest could not be
  read, or the host could not be measured.
- `validate-profile` exits 5 when the profile is not launchable, never 3
  or 4.
- `check-readiness` exits 5 when the environment is not ready, and 0 both
  when it is ready and when `ready` is null (the profile declares no checks).
- `exec` exits 0 when the command ran at all; the command's own exit status
  is reported as `exit_code` inside the result, not as the process exit code.
- `create-profile` exits 5 when the draft budget is spent without a clean
  parse.
- `manage` exits 5 when a confirmed plan did not complete (a step failed);
  0 covers both an unrun plan and a plan that ran to completion.
