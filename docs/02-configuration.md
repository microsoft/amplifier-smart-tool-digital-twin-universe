# Configuration

Every setting is an environment variable; there is no config file. Deterministic
capabilities need none of this. Model-backed capabilities
(`create_profile`, `plan_install`, `diagnose`, `manage`) need a model provider.

## Model provider and model

- `AMPLIFIER_DTU_PROVIDER`: the model provider to use, when neither the
  `--provider` flag nor the `provider=` argument names one. Must be one of
  the providers reported by `probe()`'s `model_providers`, i.e. one whose
  credential variable is set.
- `AMPLIFIER_DTU_MODEL`: the model to use, when neither `--model` nor
  `model=` names one.
- `--provider` / `--model` (CLI flags) and `provider=` / `model=` (library
  arguments) take precedence over the environment variables above, on every
  model-backed capability.

Without a resolvable provider, a model-backed capability raises
`NoProviderError` naming the remedy rather than returning a lesser
deterministic answer.

## Provider credentials

At least one of these must be set for any model-backed capability to run:

- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `GOOGLE_API_KEY`
- `AZURE_OPENAI_API_KEY`

## Provider client libraries

A provider is usable when its credential is set and its client library is
importable. The Anthropic client ships as a dependency, so a host with
`ANTHROPIC_API_KEY` set needs nothing further. The others install as extras:

```bash
uv tool install --with openai amplifier-digital-twin-universe
uv tool install --with google-genai amplifier-digital-twin-universe
```

`probe()` reports under `model_providers` only the providers that satisfy both
halves, so a credential set for a provider whose client is absent fails at
`preflight` naming the package to install rather than deep inside the engine.

## Environment limit

- `AMPLIFIER_DTU_MAX_ENVIRONMENTS`: the concurrent environment ceiling
  `launch` enforces. Default 15. `0` removes the ceiling. The `--max-environments`
  flag (CLI) and `max_environments=` argument (library) override this
  variable for a single call.

## Engine timeout

- `AMPLIFIER_DTU_INCUS_LAUNCH_TIMEOUT_SECONDS`: overrides how long the
  underlying `incus launch` is allowed to run, read by the DTU engine
  (`amplifier_bundle_digital_twin_universe`), not by this package directly.
  Default 120 seconds. Useful on a host running many containers, where
  `incus launch` can take well over the default before the daemon responds.
