#!/bin/sh
# Caddy is pure transport now: the account system (register / login) took
# over access control, so there is no shared password to hash. Start Caddy
# directly with the Caddyfile in the image.
set -eu

exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
