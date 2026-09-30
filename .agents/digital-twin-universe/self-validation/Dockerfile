# A machine with Docker on it and nothing of this repository's development setup: the daemon, its Compose plugin,
# Python, and uv, which is what the README asks a user to have.
FROM docker:dind
RUN apk add --no-cache python3 git curl ca-certificates
COPY --from=ghcr.io/astral-sh/uv:alpine /usr/local/bin/uv /usr/local/bin/uv

# The universe's certificate authority, provided by `launch` as the `dtu-ca` build context.
# The install below fetches from a github.com URL the gateway answers for, so the build has to trust it.
COPY --from=dtu-ca ca.crt /usr/local/share/ca-certificates/digital-twin-universe.crt
RUN update-ca-certificates

# The install a user would run, against the URL this checkout stands in for. The gateway sends it here.
# `exec` opens a login shell, and a login shell reads ~/.profile and not the image's ENV, so the PATH change goes
# where uv's own `update-shell` would put it.
RUN uv tool install git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe \
    && echo 'export PATH="/root/.local/bin:$PATH"' >> /root/.profile

# A profile for the installed tool to launch, in the place it looks for one.
WORKDIR /root/project
COPY sample/ .agents/digital-twin-universe/sample/
