FROM debian:bookworm-slim
# Debian's chromium is built for amd64 and arm64 alike, which Chrome for Testing is not. libnss3-tools brings
# certutil, which agent-browser needs to hand the universe's authority to Chromium.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl chromium fonts-liberation libnss3-tools \
        procps iproute2 file \
    && rm -rf /var/lib/apt/lists/*

# agent-browser's release binary, checked against its hash, and the skills it prints from the npm package of the
# same version, which the binary finds through AGENT_BROWSER_SKILLS_DIR.
ARG AGENT_BROWSER_VERSION=0.41.1
ARG TARGETARCH
RUN case "${TARGETARCH}" in \
        amd64) asset=linux-x64; sha=2a89295b62ddd497986a9a80b52e1dbe4c1f6bd68ffde67eaae82eec2206af61 ;; \
        arm64) asset=linux-arm64; sha=f0b3bdaa40abf2a5111ec08e6d242110aaf56045ef5108cbc633bacc8e53ca17 ;; \
        *) echo "agent-browser has no build for ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && curl -fsSLo /usr/local/bin/agent-browser \
        "https://github.com/vercel-labs/agent-browser/releases/download/v${AGENT_BROWSER_VERSION}/agent-browser-${asset}" \
    && echo "${sha}  /usr/local/bin/agent-browser" | sha256sum -c - \
    && chmod +x /usr/local/bin/agent-browser \
    && mkdir -p /opt/agent-browser \
    && curl -fsSL "https://registry.npmjs.org/agent-browser/-/agent-browser-${AGENT_BROWSER_VERSION}.tgz" \
        | tar -xz -C /opt/agent-browser --strip-components=1 package/skill-data package/LICENSE
ENV AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium \
    AGENT_BROWSER_SKILLS_DIR=/opt/agent-browser/skill-data \
    AGENT_BROWSER_NO_VERSION_CHECK=1

# A user rather than root, so Chromium keeps its sandbox.
RUN useradd -m user
USER user
WORKDIR /home/user
