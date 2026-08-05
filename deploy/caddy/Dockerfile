# Poliscope's edge: the only service that binds to a host port. `api` and
# `web` sit on the compose-internal network only (see docker-compose.yml), so
# the shared password set here is not something a visitor can route around by
# guessing a port.
#
#   docker build -f deploy/caddy/Dockerfile -t poliscope-caddy .
#
FROM caddy:2-alpine
COPY deploy/caddy/Caddyfile /etc/caddy/Caddyfile
COPY deploy/caddy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
