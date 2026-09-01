# Digital Twin Universe Smart Tool

A [Smart Tool](https://github.com/microsoft/amplifier-smart-tools) that stands up
isolated, realistic environments from a declarative profile, so software can be tested
as though actually deployed. Every capability lives in the `amplifier_digital_twin_universe`
library; the CLI is a thin wrapper over it and adds nothing of its own.

## Installation

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/), and
[Incus](docs/installing-incus.md) to launch anything.

```bash
# as a CLI
uv tool install git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe

# as a library
uv add "amplifier-digital-twin-universe @ git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe"

# as a skill, for a coding agent
npx skills add microsoft/amplifier-smart-tool-digital-twin-universe
```

Verify with `amplifier-digital-twin-universe manifest`, which needs no prerequisites
and no credentials. To upgrade, run `uv tool upgrade amplifier-digital-twin-universe`.

## Interface

```bash
# what this host can do, and ordered steps to fix what it cannot
amplifier-digital-twin-universe check
amplifier-digital-twin-universe install --goal "test a web service"

# author a profile from a description, and check one you already have
amplifier-digital-twin-universe create-profile --description "a FastAPI app on port 8000" --out profile.yaml
amplifier-digital-twin-universe validate-profile --file profile.yaml

# the environment lifecycle
amplifier-digital-twin-universe launch --profile profile.yaml
amplifier-digital-twin-universe list
amplifier-digital-twin-universe status --id dtu-1a2b3c4d
amplifier-digital-twin-universe check-readiness --id dtu-1a2b3c4d
amplifier-digital-twin-universe exec --id dtu-1a2b3c4d --command "curl -sf localhost:8000/health"
amplifier-digital-twin-universe file-push --id dtu-1a2b3c4d --source ./src --destination /workspace --recursive
amplifier-digital-twin-universe file-pull --id dtu-1a2b3c4d --source /var/log/app.log --destination ./
amplifier-digital-twin-universe update --id dtu-1a2b3c4d
amplifier-digital-twin-universe destroy --id dtu-1a2b3c4d

# work out what is wrong, and drive the lifecycle from a request in words
amplifier-digital-twin-universe doctor --symptom "the container has no outbound network"
amplifier-digital-twin-universe manage --request "tear down every stopped environment" --confirmed
```

Every result is one JSON document on stdout. `-h` is the terse summary for a person;
`--help` is the complete listing for an agent. See the
[CLI reference](docs/03-cli.md) for every flag, and the
[library reference](docs/01-library.md) for the same capabilities as Python.

## Configuration

`create-profile`, `install`, `doctor`, and `manage` are model-backed and need a model
provider configured in the environment, such as `ANTHROPIC_API_KEY`. Everything else is
deterministic and needs nothing. See
[docs/02-configuration.md](docs/02-configuration.md) for provider and model selection
and the environment variables this tool reads.

## Development

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Contributing

> [!NOTE]
> This project is not currently accepting external contributions, but we're actively working toward opening this up. We value community input and look forward to collaborating in the future. For now, feel free to fork and experiment!

Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit [Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.