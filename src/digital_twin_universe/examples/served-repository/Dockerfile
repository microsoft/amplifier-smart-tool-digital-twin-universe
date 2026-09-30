FROM alpine:3.20
RUN apk add --no-cache git curl ca-certificates

# The universe's certificate authority, which `launch` provides as the `dtu-ca` build context.
# A build reaches the gateway on its own, but trusts it only where the Dockerfile says to: BuildKit passes the
# proxy variables without being asked, and nothing else, so the certificate has to be copied in like this.
# Leave these two lines out and the universe still works at runtime, where `launch` mounts the same file.
COPY --from=dtu-ca ca.crt /usr/local/share/ca-certificates/digital-twin-universe.crt
RUN update-ca-certificates

RUN adduser -D user
USER user
WORKDIR /home/user

# The point of the profile: a URL that belongs to GitHub, answered by the checkout on this machine.
ARG REPOSITORY_URL
RUN git clone "${REPOSITORY_URL}" /home/user/repo
