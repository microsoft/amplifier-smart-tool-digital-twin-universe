FROM python:3.12-alpine
RUN adduser -D user
USER user
WORKDIR /home/user/site
# The pages are copied in, not mounted: the twin has what a deployment would have and nothing from this machine.
COPY --chown=user site/ .
