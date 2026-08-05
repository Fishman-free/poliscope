#!/bin/sh
# Caddy's `basic_auth` directive wants a bcrypt hash, not a plaintext
# password -- but the researcher should only ever have to write one plaintext
# password into .env. So this hashes POLISCOPE_SITE_PASSWORD on every
# container start (cheap, no state to persist) and hands the hash to Caddy
# through an env var the Caddyfile references. See deploy/caddy/Dockerfile.
set -eu

export POLISCOPE_SITE_PASSWORD_HASH="$(caddy hash-password --plaintext "${POLISCOPE_SITE_PASSWORD}")"

exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
