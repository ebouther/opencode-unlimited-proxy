#!/bin/bash
# Entrypoint for the WARP rotator node.
#
# Derived from alztrk/opencode-ip-rotator's Dockerfile.warp inline entrypoint,
# with one change: the Cloudflare terms of service are no longer accepted
# silently on the operator's behalf. They must be accepted explicitly.
#
#   WARP_ACCEPT_TOS=1
#
# Read https://www.cloudflare.com/application/terms/ before setting it. Relevant
# excerpt (Application Terms of Service, section 3 "VPN Profile"):
#
#   "Although the Application will need to install a VPN profile on your device
#    and operates similarly to a VPN product or service, the Application is not
#    designed to hide your identity from the Internet properties you access from
#    your device."
#
# Section 6 also forbids using the Application to "misrepresent or act on behalf
# of others" and to "create an undue burden on the Application or the networks".

set -euo pipefail

if [ "${WARP_ACCEPT_TOS:-0}" != "1" ]; then
  cat >&2 <<'MSG'
WARP terms of service were not accepted.

This image runs the Cloudflare WARP client, which is governed by the Cloudflare
Application Terms of Service: https://www.cloudflare.com/application/terms/

If you have read and accept those terms, opt in explicitly:

    echo 'WARP_ACCEPT_TOS=1' > .env      # next to docker-compose.yml
    docker compose up -d

Note that Cloudflare states the WARP client "is not designed to hide your
identity" from the sites you reach. Using it to appear as several different
users is outside that intent, and Cloudflare may terminate accounts without
notice for violations.

Using a different egress source? You do not need this image: point the
"proxies" array in config.json at your own HTTP/SOCKS proxies and drop the
alztrk-warp-* services from the compose file.
MSG
  exit 1
fi

service dbus start
warp-svc &
sleep 3

# Registration must be idempotent. A node that already has a valid registration
# (reg.json present in its writable layer, which survives `docker compose up`
# that restarts rather than recreates the container) must NOT be forced through
# `registration new`: that command fails with
#   "Old registration is still around. Try running: warp-cli registration delete"
# and the previous version of this loop treated every failure as a transient
# Cloudflare 429, retried 12 times, then exited 1 without ever reaching
# `exec python3 rotator.py`. Consequence: the rotator's HTTP control plane on
# port 8001 never started, the dedupe arbiter could no longer rotate colliding
# nodes and could only drain them, and the pool collapsed from six to two.
#
# So: if a registration already exists, skip `registration new` entirely. If a
# `registration new` fails because one already exists, treat that as success.
# Only genuine transient failures (429, network) are retried with a widening
# delay, so a real rate limit is still ridden out instead of crash-looping.
registered=0
if warp-cli --accept-tos registration show >/dev/null 2>&1; then
    echo "entrypoint: existing registration found, skipping registration new." >&2
    registered=1
fi

attempt=1
until [ "$registered" -eq 1 ]; do
    set +e
    output=$(warp-cli --accept-tos registration new 2>&1)
    rc=$?
    set -e
    if [ "$rc" -eq 0 ]; then
        registered=1
        break
    fi
    # Permanent condition, not a 429: the node already has a registration.
    if printf '%s' "$output" | grep -qi "Old registration is still around"; then
        echo "entrypoint: existing registration present; continuing." >&2
        registered=1
        break
    fi
    if [ "$attempt" -ge "${WARP_REGISTRATION_ATTEMPTS:-12}" ]; then
        echo "entrypoint: giving up on registration after ${attempt} attempts." >&2
        printf '%s\n' "$output" >&2
        echo "entrypoint: WARP registration failed (rate limit or permanent error)." >&2
        echo "entrypoint: Wait a few minutes, then: docker compose restart" >&2
        exit 1
    fi
    delay=$((attempt * 15))
    [ "$delay" -gt 120 ] && delay=120
    echo "entrypoint: registration attempt ${attempt} failed, retrying in ${delay}s" >&2
    printf '%s\n' "$output" >&2
    sleep "$delay"
    attempt=$((attempt + 1))
done

warp-cli --accept-tos mode warp
warp-cli --accept-tos connect
sleep 3

exec python3 rotator.py