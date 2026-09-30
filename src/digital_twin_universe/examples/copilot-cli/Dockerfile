FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates git
RUN useradd -m user
# exec runs as this user, in WORKDIR.
USER user
WORKDIR /home/user
RUN curl -fsSL https://gh.io/copilot-install | bash
ENV PATH="/home/user/.local/bin:${PATH}"
