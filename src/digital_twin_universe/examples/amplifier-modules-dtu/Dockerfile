# --- amplifier-core, built from the served repository.
# To use the released amplifier-core from PyPI instead, delete this stage, the COPY --from=amplifier-core and
# UV_FIND_LINKS lines below, and the amplifier-core entry under `repositories` in compose.yaml.
FROM rust:1-bookworm AS amplifier-core
RUN apt-get update && apt-get install -y --no-install-recommends python3 git ca-certificates curl
# The universe's certificate authority, so the clone below trusts the gateway that sends it to the local checkout.
COPY --from=dtu-ca ca.crt /usr/local/share/ca-certificates/digital-twin-universe.crt
RUN update-ca-certificates
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
ENV UV_NATIVE_TLS=true
RUN git clone https://github.com/microsoft/amplifier-core /src
WORKDIR /src
# uvx rather than `uv run --with maturin`, which would sync the whole project first.
RUN uvx maturin build --release --out /wheels
# --- end of the amplifier-core stage.

FROM ubuntu:24.04
# python3 because without a system Python uv picks the newest it can download, for which tree-sitter, used by
# tool-bash, publishes no wheel.
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates python3
COPY --from=dtu-ca ca.crt /usr/local/share/ca-certificates/digital-twin-universe.crt
RUN update-ca-certificates
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
COPY --from=amplifier-core /wheels /opt/amplifier-core-wheels
RUN useradd -m user
# exec runs as this user, in WORKDIR.
USER user
WORKDIR /home/user
# UV_FIND_LINKS stays set at runtime, so the modules Amplifier installs on first run resolve the same local core.
# uv takes the local wheel only when its version is above the newest amplifier-core on PyPI; bump it if not.
ENV PATH="/home/user/.local/bin:${PATH}" \
    UV_NATIVE_TLS=true \
    UV_FIND_LINKS=/opt/amplifier-core-wheels
RUN uv tool install git+https://github.com/microsoft/amplifier
# `${ANTHROPIC_API_KEY}` is written literally and expanded by Amplifier at runtime, so the key is never in the image.
# The provider's `@main` must be the branch the local checkout has checked out, the only one served.
RUN mkdir -p .amplifier && cat > .amplifier/settings.yaml <<'YAML'
config:
  providers:
    - module: provider-anthropic
      source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
      config:
        api_key: ${ANTHROPIC_API_KEY}
        default_model: claude-sonnet-5-5
YAML
